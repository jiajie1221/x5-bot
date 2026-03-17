from flask import Flask, request, jsonify
from apscheduler.schedulers.background import BackgroundScheduler
import time
import logging
import random
from datetime import datetime

import config
import database
import polymarket

# 初始化 Flask 应用
app = Flask(__name__)

# 配置日志
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# 初始化数据库
database.init_db()

# 市场列表
MARKETS = ["BTC-5M", "ETH-5M"]

# ---------- 辅助函数 ----------
def calculate_bet_amount(initial_bet, stage):
    """
    根据阶段计算下注金额（马丁格尔策略）
    第一注：1x
    第二注：2x
    第三注：4x
    """
    if stage == 1:
        return initial_bet
    elif stage == 2:
        return initial_bet * 2
    elif stage == 3:
        return initial_bet * 4
    else:
        return 0

def get_asset_from_market(market):
    """从市场名称获取资产类型"""
    return market.split('-')[0]  # "BTC-5M" -> "BTC"

def get_direction_from_action(action):
    """从 action 获取方向"""
    return "UP" if action == "UP" else "DOWN"

# ---------- Webhook：接收 TradingView 信号 ----------
@app.route('/webhook', methods=['POST'])
def webhook():
    """
    接收 TradingView 的 Webhook 信号
    期望的 JSON 格式：
    {
        "market": "BTC-5M",
        "action": "UP" 或 "DOWN"
    }
    """
    # 可选：验证 secret
    if config.WEBHOOK_SECRET:
        provided_secret = request.headers.get('X-Secret')
        if provided_secret != config.WEBHOOK_SECRET:
            logger.warning("未授权的 webhook 请求")
            return "Unauthorized", 401

    # 获取请求数据
    data = request.get_json()
    if not data:
        logger.error("无效的 JSON 数据")
        return "Invalid JSON", 400

    market = data.get('market')
    action = data.get('action')
    
    logger.info(f"📡 收到信号: {market} - {action}")

    # 验证数据有效性
    if market not in MARKETS:
        logger.error(f"无效的市场: {market}")
        return f"Invalid market: {market}", 400
        
    if action not in ["UP", "DOWN"]:
        logger.error(f"无效的动作: {action}")
        return f"Invalid action: {action}", 400

    # 处理信号
    try:
        process_signal(market, action)
        return "OK", 200
    except Exception as e:
        logger.error(f"处理信号时出错: {e}")
        return "Internal Error", 500

def process_signal(market, action):
    """核心逻辑：根据当前状态决定下注"""
    logger.info(f"🔄 处理 {market} 信号: {action}")
    
    # 加载当前回合状态
    round_data = database.load_round(market)

    # 如果没有活跃回合，开始新回合
    if not round_data:
        logger.info(f"📊 {market} 无活跃回合，开始新回合")
        start_new_round(market, action)
        return

    # 如果有活跃回合，检查状态
    status = round_data["status"]
    logger.info(f"📊 {market} 当前状态: 阶段 {round_data['stage']}, 状态 {status}")
    
    if status == "active":
        # 上一注还在等待结算，忽略本次信号
        logger.info(f"⏳ {market} 回合进行中，忽略信号")
        return
        
    elif status == "waiting_next":
        # 上一注已输，等待加仓
        # 强制使用原方向，忽略信号方向
        direction = round_data["direction"]
        stage = round_data["stage"] + 1
        
        # 检查是否达到最大加仓次数
        if stage > config.MAX_STAGES:
            logger.info(f"🛑 {market} 已达最大加仓次数 {config.MAX_STAGES}，本轮结束")
            database.delete_round(market)
            # 根据新信号重新开始
            start_new_round(market, action)
            return

        initial_bet = round_data["initial_bet"]
        bet_amount = calculate_bet_amount(initial_bet, stage)

        logger.info(f"🎯 {market} 第{stage}注加仓: {bet_amount} USDC")
        
        # 执行下注（方向固定为原方向）
        place_bet(market, direction, bet_amount, stage, initial_bet)
        
    else:
        # 异常状态，重置
        logger.warning(f"⚠️ {market} 异常状态 {status}，重置")
        database.delete_round(market)
        start_new_round(market, action)

def start_new_round(market, action):
    """开始新回合（第一注）"""
    direction = get_direction_from_action(action)
    initial_bet = config.INITIAL_CAPITAL * config.BASE_BET_PERCENT
    bet_amount = initial_bet  # 第一注
    
    logger.info(f"🆕 {market} 新回合: {direction}, 第一注 {bet_amount} USDC")
    
    place_bet(market, direction, bet_amount, stage=1, initial_bet=initial_bet)

def place_bet(market, direction, amount, stage, initial_bet):
    """执行下注并保存状态"""
    logger.info(f"💰 开始下单: {market} - {direction} - {amount} USDC (第{stage}注)")
    
    # 1. 获取下一个市场信息
    asset = get_asset_from_market(market)
    market_info = polymarket.get_next_market(asset)
    
    if not market_info:
        logger.error(f"❌ {market} 无法获取市场信息，取消下单")
        return

    condition_id = market_info.get("condition_id")
    if not condition_id:
        logger.error(f"❌ {market} 市场信息缺少 condition_id")
        return

    # 2. 获取 token ID
    token_id = polymarket.get_token_id(market_info, direction)
    if not token_id:
        logger.error(f"❌ {market} 无法获取 token ID")
        return

    # 3. 获取当前最优卖价
    ask_price = polymarket.get_order_book(condition_id, token_id)
    if ask_price is None:
        logger.error(f"❌ {market} 无法获取订单簿")
        return

    # 4. 根据价格决定下单类型
    # 价格偏离计算
    deviation = abs(ask_price - config.TARGET_PRICE)
    
    if deviation <= config.PRICE_TOLERANCE:
        # 在容忍范围内，使用市价单
        order_type = "market"
        price = ask_price
        logger.info(f"📈 {market} 价格 {ask_price} 在容忍范围内，使用市价单")
    else:
        # 超出容忍，挂限价单在目标价
        order_type = "limit"
        price = config.TARGET_PRICE
        logger.info(f"📉 {market} 价格 {ask_price} 超出容忍，挂限价单在 {price}")

    # 5. 下单
    side = "BUY"  # 我们总是买入（YES 或 NO）
    order_result = polymarket.place_order(
        condition_id=condition_id,
        token_id=token_id,
        side=side,
        amount=amount,
        price=price,
        order_type=order_type
    )

    if not order_result or not order_result.get("id"):
        logger.error(f"❌ {market} 下单失败")
        return

    order_id = order_result["id"]

    # 6. 保存回合状态
    round_data = {
        "direction": direction,
        "stage": stage,
        "initial_bet": initial_bet,
        "last_bet_id": order_id,
        "last_bet_amount": amount,
        "last_bet_time": int(time.time()),
        "status": "active"  # 刚下完，等待结算
    }
    
    database.save_round(market, round_data)
    logger.info(f"✅ {market} 下单成功，订单ID: {order_id}")

# ---------- 定时任务：检查市场结算 ----------
def check_settlements():
    """轮询所有市场，检查是否已结算"""
    logger.info("🔍 开始检查市场结算状态...")
    
    for market in MARKETS:
        try:
            round_data = database.load_round(market)
            if not round_data or round_data["status"] != "active":
                continue

            logger.info(f"📊 检查 {market} 回合状态")
            
            # 获取该市场的条件ID
            asset = get_asset_from_market(market)
            market_info = polymarket.get_next_market(asset)
            
            if not market_info:
                logger.warning(f"⚠️ {market} 无法获取市场信息")
                continue

            condition_id = market_info.get("condition_id")
            
            # 查询市场结果
            outcome = polymarket.get_market_outcome(condition_id)
            
            if outcome is None:
                # 市场尚未结算
                logger.info(f"⏳ {market} 尚未结算")
                continue

            # 判断是否获胜
            # 注意：这里需要根据实际返回的 outcome 来判断
            # 假设 outcome 是 "Yes" 或 "No"
            won = False
            if round_data["direction"] == "UP" and outcome == "Yes":
                won = True
            elif round_data["direction"] == "DOWN" and outcome == "No":
                won = True

            if won:
                # 赢了：清除回合
                logger.info(f"🎉 {market} 获胜！清除回合")
                database.delete_round(market)
            else:
                # 输了：状态改为 waiting_next
                logger.info(f"😢 {market} 失败，等待下次信号加仓")
                round_data["status"] = "waiting_next"
                database.save_round(market, round_data)
                
        except Exception as e:
            logger.error(f"❌ 检查 {market} 结算时出错: {e}")

# ---------- 测试用路由 ----------
@app.route('/health', methods=['GET'])
def health():
    """健康检查"""
    return jsonify({
        "status": "ok",
        "time": datetime.now().isoformat(),
        "markets": MARKETS
    })

@app.route('/status/<market>', methods=['GET'])
def get_status(market):
    """获取指定市场的当前状态"""
    if market not in MARKETS:
        return jsonify({"error": "Invalid market"}), 400
    
    round_data = database.load_round(market)
    if round_data:
        return jsonify(round_data)
    else:
        return jsonify({"status": "no_active_round"})

# ---------- 主函数 ----------
if __name__ == '__main__':
    logger.info("🚀 启动 Polymarket 交易机器人")
    logger.info(f"📊 监控市场: {MARKETS}")
    logger.info(f"💰 初始资金: {config.INITIAL_CAPITAL} USDC")
    logger.info(f"📈 第一注比例: {config.BASE_BET_PERCENT*100}%")
    logger.info(f"🔄 最大加仓次数: {config.MAX_STAGES}")
    
    # 测试 Polymarket 连接
    if not polymarket.test_connection():
        logger.error("❌ 无法连接到 Polymarket API，退出")
        exit(1)
    
    # 启动调度器（每30秒检查一次结算）
    scheduler = BackgroundScheduler()
    scheduler.add_job(func=check_settlements, trigger="interval", seconds=30)
    scheduler.start()
    logger.info("✅ 结算检查调度器已启动")
    
    # 启动 Flask 应用 - 使用环境变量 PORT 或默认 5001（Railway 会自动映射）
    port = int(os.getenv("PORT", 5001))
    logger.info(f"🌐 启动 Webhook 服务器，端口 {port}")
    app.run(host='0.0.0.0', port=port, debug=False)
