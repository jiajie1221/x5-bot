from flask import Flask, request, jsonify
from apscheduler.schedulers.background import BackgroundScheduler
import time
import logging
import os
from datetime import datetime
import requests
import sqlite3
from web3 import Web3
from eth_account.messages import encode_defunct

# ========== 配置直接写在这里 ==========
from dotenv import load_dotenv

load_dotenv()

PRIVATE_KEY = os.getenv("POLYGON_PRIVATE_KEY")
RPC_URL = os.getenv("POLYGON_RPC_URL")
INITIAL_CAPITAL = float(os.getenv("INITIAL_CAPITAL", 100))
BASE_BET_PERCENT = float(os.getenv("BASE_BET_PERCENT", 0.02))
MAX_STAGES = int(os.getenv("MAX_STAGES", 3))
TARGET_PRICE = float(os.getenv("TARGET_PRICE", 0.5))
PRICE_TOLERANCE = float(os.getenv("PRICE_TOLERANCE", 0.025))
WEBHOOK_SECRET = os.getenv("WEBHOOK_SECRET", "")

# ========== 数据库函数 ==========
DB_PATH = "state.db"

def init_db():
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute('''
        CREATE TABLE IF NOT EXISTS rounds (
            market TEXT PRIMARY KEY,
            direction TEXT,
            stage INTEGER,
            initial_bet REAL,
            last_bet_id TEXT,
            last_bet_amount REAL,
            last_bet_time INTEGER,
            status TEXT
        )
    ''')
    conn.commit()
    conn.close()
    print("✅ 数据库初始化完成")

def load_round(market):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("SELECT direction, stage, initial_bet, last_bet_id, last_bet_amount, last_bet_time, status FROM rounds WHERE market=?", (market,))
    row = c.fetchone()
    conn.close()
    if row:
        return {
            "direction": row[0],
            "stage": row[1],
            "initial_bet": row[2],
            "last_bet_id": row[3],
            "last_bet_amount": row[4],
            "last_bet_time": row[5],
            "status": row[6]
        }
    return None

def save_round(market, data):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute('''
        REPLACE INTO rounds (market, direction, stage, initial_bet, last_bet_id, last_bet_amount, last_bet_time, status)
        VALUES (?,?,?,?,?,?,?,?)
    ''', (
        market,
        data["direction"],
        data["stage"],
        data["initial_bet"],
        data.get("last_bet_id", ""),
        data.get("last_bet_amount", 0),
        data.get("last_bet_time", 0),
        data["status"]
    ))
    conn.commit()
    conn.close()

def delete_round(market):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("DELETE FROM rounds WHERE market=?", (market,))
    conn.commit()
    conn.close()

# ========== Polymarket API 函数 ==========
CLOB_API = "https://clob.polymarket.com"

def test_connection():
    try:
        resp = requests.get(f"{CLOB_API}/markets", params={"limit": 1})
        if resp.status_code == 200:
            print("✅ 成功连接到 Polymarket API")
            return True
        else:
            print(f"❌ 连接失败: HTTP {resp.status_code}")
            return False
    except Exception as e:
        print(f"❌ 连接异常: {e}")
        return False

def get_next_market(asset, duration=5):
    """
    获取下一个5分钟市场，基于 Gamma API 返回的真实 slug 格式。
    真实 slug 格式示例：eth-updown-5m-1773834600
    """
    try:
        import time
        from datetime import datetime

        # 资产名称映射：实际slug中用的是小写简称，如 eth, btc
        asset_map = {
            "BTC": "btc",
            "ETH": "eth"
        }
        asset_short = asset_map.get(asset)
        if not asset_short:
            print(f"❌ 不支持的资产: {asset}")
            return None

        gamma_url = "https://gamma-api.polymarket.com/markets"
        params = {
            "active": "true",
            "closed": "false",
            "limit": 200,
            "order": "startDate",
            "ascending": "false",
            "tag": "crypto"
        }

        print(f"\n🔍 正在从 Gamma API 拉取加密货币市场...")
        resp = requests.get(gamma_url, params=params, timeout=15)

        if resp.status_code != 200:
            print(f"❌ Gamma API 请求失败，状态码: {resp.status_code}")
            return None

        markets = resp.json()
        print(f"📊 Gamma API 返回 {len(markets)} 个活跃市场")

        # 构造匹配模式：如 "eth-updown-5m-"
        pattern = f"{asset_short}-updown-{duration}m-"
        five_min_markets = []

        for m in markets:
            slug = m.get('slug', '')
            if pattern in slug:
                # 提取时间戳（slug 末尾部分）
                try:
                    ts_str = slug.split('-')[-1]
                    start_ts = int(ts_str)
                except:
                    # 如果提取失败，使用 startDate 字段
                    start_date = m.get('startDate')
                    if start_date:
                        start_ts = int(datetime.fromisoformat(start_date.replace('Z', '+00:00')).timestamp())
                    else:
                        start_ts = 0

                five_min_markets.append({
                    'slug': slug,
                    'question': m.get('question'),
                    'conditionId': m.get('conditionId'),
                    'tokens': m.get('tokens', []),
                    'start_ts': start_ts,
                    'market': m
                })

        print(f"📋 找到 {len(five_min_markets)} 个符合 {pattern} 格式的市场")

        if not five_min_markets:
            print("❌ 未找到任何匹配的市场")
            return None

        # 按开始时间升序排序
        five_min_markets.sort(key=lambda x: x['start_ts'])

        now_ts = int(time.time())
        next_market = None

        # 寻找下一个未来窗口（start_ts > now_ts）
        for m in five_min_markets:
            if m['start_ts'] > now_ts:
                next_market = m
                break

        if not next_market and five_min_markets:
            # 如果没有未来的，取最后一个（可能正在进行）
            next_market = five_min_markets[-1]
            print("⚠️ 未找到未来的市场，使用最后一个（可能正在进行）")

        if next_market:
            print(f"✅ 选择市场: {next_market['slug']}")
            print(f"   - 问题: {next_market['question']}")
            print(f"   - 条件ID: {next_market['conditionId']}")
            start_time_str = datetime.fromtimestamp(next_market['start_ts']).strftime('%Y-%m-%d %H:%M:%S')
            print(f"   - 开始时间: {start_time_str} UTC")
            return next_market['market']
        else:
            print("❌ 无法选择合适的市场")
            return None

    except Exception as e:
        print(f"❌ 获取市场时发生异常: {e}")
        import traceback
        traceback.print_exc()
        return None

def get_token_id(market_info, side):
    """
    从 Gamma API 返回的市场数据中提取 token ID。
    调试版本：打印 market_info 中 tokens 字段的详细结构。
    """
    try:
        print("\n🔍 [get_token_id 调试] 开始提取 token ID")
        print(f"market_info 的 keys: {list(market_info.keys())}")
        
        # 获取 tokens 字段
        tokens = market_info.get('tokens')
        print(f"tokens 字段类型: {type(tokens)}")
        
        if tokens is None:
            print("❌ tokens 字段不存在")
            return None
            
        if isinstance(tokens, list):
            print(f"tokens 列表长度: {len(tokens)}")
            for i, token in enumerate(tokens):
                print(f"token[{i}] 类型: {type(token)}")
                if isinstance(token, dict):
                    print(f"token[{i}] 的 keys: {list(token.keys())}")
                    print(f"token[{i}] 的部分内容: id={token.get('id')}, outcome={token.get('outcome')}")
                else:
                    print(f"token[{i}] 不是字典，而是: {token}")
        else:
            print(f"tokens 不是列表，而是: {tokens}")
            # 如果 tokens 是其他结构，尝试直接返回
            if isinstance(tokens, dict) and tokens.get('id'):
                return tokens.get('id')
        
        # 尝试多种方式提取 ID
        # 方式1：如果 tokens 是列表，尝试按 outcome 匹配
        if isinstance(tokens, list) and len(tokens) >= 2:
            # 尝试匹配 outcome
            for token in tokens:
                if isinstance(token, dict):
                    outcome = token.get('outcome', '').lower()
                    token_id = token.get('id')
                    if side == "UP" and outcome == "yes":
                        return token_id
                    elif side == "DOWN" and outcome == "no":
                        return token_id
            
            # 如果没匹配到，按顺序：第一个是 Yes，第二个是 No
            if side == "UP":
                return tokens[0].get('id') if isinstance(tokens[0], dict) else None
            else:
                return tokens[1].get('id') if isinstance(tokens[1], dict) else None
        
        print("❌ 无法从 tokens 中提取 ID")
        return None
        
    except Exception as e:
        print(f"❌ get_token_id 异常: {e}")
        import traceback
        traceback.print_exc()
        return None

def get_order_book(condition_id, token_id):
    try:
        resp = requests.get(f"{CLOB_API}/order-book", params={"condition_id": condition_id, "token_id": token_id})
        if resp.status_code == 200:
            data = resp.json()
            asks = data.get("asks", [])
            if asks and len(asks) > 0:
                return float(asks[0]["price"])
    except Exception as e:
        print(f"❌ 获取订单簿失败: {e}")
    return None

def place_order(condition_id, token_id, side, amount, price, order_type="limit"):
    print(f"📝 模拟下单: {amount} USDC 于 {price}")
    return {"id": f"order_{int(time.time())}"}

def get_market_outcome(condition_id):
    return None

# ========== Flask 应用 ==========
app = Flask(__name__)

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

init_db()

MARKETS = ["BTC-5M", "ETH-5M"]

def calculate_bet_amount(initial_bet, stage):
    if stage == 1:
        return initial_bet
    elif stage == 2:
        return initial_bet * 2
    elif stage == 3:
        return initial_bet * 4
    return 0

def get_asset_from_market(market):
    """从市场名称获取资产类型"""
    if market == "BTC-5M":
        return "BTC"
    elif market == "ETH-5M":
        return "ETH"
    return market.split('-')[0]

def get_direction_from_action(action):
    return "UP" if action == "UP" else "DOWN"

@app.route('/webhook', methods=['POST'])
def webhook():
    if WEBHOOK_SECRET:
        provided_secret = request.headers.get('X-Secret')
        if provided_secret != WEBHOOK_SECRET:
            return "Unauthorized", 401

    data = request.get_json()
    if not data:
        return "Invalid JSON", 400

    market = data.get('market')
    action = data.get('action')
    
    logger.info(f"📡 收到信号: {market} - {action}")

    if market not in MARKETS or action not in ["UP", "DOWN"]:
        return f"Invalid market/action", 400

    try:
        process_signal(market, action)
        return "OK", 200
    except Exception as e:
        logger.error(f"处理信号时出错: {e}")
        return "Internal Error", 500

def process_signal(market, action):
    logger.info(f"🔄 处理 {market} 信号: {action}")
    round_data = load_round(market)

    if not round_data:
        logger.info(f"📊 {market} 无活跃回合，开始新回合")
        start_new_round(market, action)
        return

    status = round_data["status"]
    
    if status == "active":
        logger.info(f"⏳ {market} 回合进行中，忽略信号")
        return
    elif status == "waiting_next":
        direction = round_data["direction"]
        stage = round_data["stage"] + 1
        
        if stage > MAX_STAGES:
            logger.info(f"🛑 {market} 已达最大加仓次数")
            delete_round(market)
            start_new_round(market, action)
            return

        initial_bet = round_data["initial_bet"]
        bet_amount = calculate_bet_amount(initial_bet, stage)
        place_bet(market, direction, bet_amount, stage, initial_bet)

def start_new_round(market, action):
    direction = get_direction_from_action(action)
    initial_bet = INITIAL_CAPITAL * BASE_BET_PERCENT
    place_bet(market, direction, initial_bet, 1, initial_bet)

def place_bet(market, direction, amount, stage, initial_bet):
    logger.info(f"💰 开始下单: {market} - {direction} - {amount} USDC")
    
    asset = get_asset_from_market(market)
    market_info = get_next_market(asset)
    
    if not market_info:
        logger.error(f"❌ {market} 无法获取市场信息")
        return

    condition_id = market_info.get("condition_id")
    token_id = get_token_id(market_info, direction)
    
    if not token_id:
        logger.error(f"❌ {market} 无法获取 token ID")
        return

    ask_price = get_order_book(condition_id, token_id)
    if ask_price is None:
        logger.error(f"❌ {market} 无法获取订单簿")
        return

    deviation = abs(ask_price - TARGET_PRICE)
    
    if deviation <= PRICE_TOLERANCE:
        order_type = "market"
        price = ask_price
    else:
        order_type = "limit"
        price = TARGET_PRICE

    order_result = place_order(condition_id, token_id, "BUY", amount, price, order_type)

    if order_result:
        round_data = {
            "direction": direction,
            "stage": stage,
            "initial_bet": initial_bet,
            "last_bet_id": order_result.get("id", ""),
            "last_bet_amount": amount,
            "last_bet_time": int(time.time()),
            "status": "active"
        }
        save_round(market, round_data)
        logger.info(f"✅ {market} 下单成功")

def check_settlements():
    logger.info("🔍 检查市场结算...")
    for market in MARKETS:
        round_data = load_round(market)
        if round_data and round_data["status"] == "active":
            if time.time() > round_data["last_bet_time"] + 350:
                delete_round(market)
                logger.info(f"🎉 {market} 回合结束")

@app.route('/health', methods=['GET'])
def health():
    return jsonify({"status": "ok", "time": datetime.now().isoformat(), "markets": MARKETS})

if __name__ == '__main__':
    logger.info("🚀 启动 Polymarket 交易机器人")
    logger.info(f"💰 初始资金: {INITIAL_CAPITAL} USDC")
    
    test_connection()
    
    scheduler = BackgroundScheduler()
    scheduler.add_job(func=check_settlements, trigger="interval", seconds=30)
    scheduler.start()
    
    port = int(os.getenv("PORT", 5001))
    app.run(host='0.0.0.0', port=port, debug=False)
