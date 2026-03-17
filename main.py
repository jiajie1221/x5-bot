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
    获取当前或下一个5分钟市场（改进版，支持多种命名）
    """
    try:
        # ==== 调试：看看API返回的市场 ====
        debug_resp = requests.get(f"{CLOB_API}/markets", params={"limit": 50})
        if debug_resp.status_code == 200:
            debug_data = debug_resp.json()
            print("📋 最近的市场列表（前15个）：")
            for i, m in enumerate(debug_data.get("data", [])[:15]):
                print(f"  {i+1}. {m.get('slug')}")
        # ==== 调试结束 ====
        
        now = int(time.time())
        current_window_start = (now // 300) * 300
        
        print(f"🔍 当前时间戳: {now}, 当前窗口开始: {current_window_start}")
        print(f"🕒 当前窗口时间: {time.strftime('%Y-%m-%d %H:%M:%S', time.gmtime(current_window_start))}")
        
        # 尝试多种可能的资产名称
        possible_names = [asset.lower()]
        if asset == "ETH":
            possible_names.append("ethereum")
        elif asset == "BTC":
            possible_names.append("bitcoin")
        
        print(f"🔎 尝试搜索名称: {possible_names}")
        
        # 获取最近的市场列表
        resp = requests.get(
            f"{CLOB_API}/markets",
            params={"limit": 200}
        )
        
        if resp.status_code == 200:
            data = resp.json()
            markets = data.get("data", [])
            
            matching_markets = []
            
            for m in markets:
                slug = m.get("slug", "").lower()
                for name in possible_names:
                    if name in slug and f"{duration}m" in slug:
                        matching_markets.append(m)
                        print(f"📌 找到匹配市场: {slug}")
                        break
            
            if matching_markets:
                # 按slug排序（最新的在前）
                matching_markets.sort(key=lambda x: x.get('slug', ''), reverse=True)
                latest = matching_markets[0]
                print(f"✅ 使用市场: {latest.get('slug')}")
                return latest
        
        print(f"❌ 未找到任何 {asset} 的 {duration}分钟市场")
        return None
        
    except Exception as e:
        print(f"❌ 获取市场失败: {e}")
        return None

def get_token_id(market_info, side):
    try:
        tokens = market_info.get("tokens", [])
        if not tokens or len(tokens) < 2:
            return None
        if side == "UP":
            return tokens[0].get("token_id")
        else:
            return tokens[1].get("token_id")
    except Exception as e:
        print(f"❌ 获取 token ID 失败: {e}")
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
    """从市场名称获取资产类型（处理不同命名）"""
    if market == "BTC-5M":
        return "BTC"
    elif market == "ETH-5M":
        return "ETH"  # 先保持 ETH，让调试代码告诉我们正确命名
    else:
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
