import requests
import time
import json
from web3 import Web3
from eth_account.messages import encode_defunct
import config

# 初始化 Web3
w3 = Web3(Web3.HTTPProvider(config.RPC_URL))
account = w3.eth.account.from_key(config.PRIVATE_KEY)
address = account.address

# Polymarket CLOB API 地址
CLOB_API = "https://clob.polymarket.com"

def get_next_market(asset, duration=5):
    """
    获取下一个即将开始的5分钟市场（改进版）
    asset: "BTC" 或 "ETH"
    duration: 分钟数，默认5
    """
    try:
        # 先尝试用 slug 精确查找（最快）
        now = int(time.time())
        start = ((now // 300) + 1) * 300  # 下一个5分钟开始时间戳
        time_str = time.strftime('%Y-%m-%dT%H:%M', time.gmtime(start))
        slug = f"{asset.lower()}-{duration}m-{time_str}Z"
        
        print(f"🔍 尝试精确查找市场: {slug}")
        resp = requests.get(
            f"{CLOB_API}/markets",
            params={"slug": slug}
        )
        
        if resp.status_code == 200:
            markets = resp.json()
            if markets and len(markets) > 0:
                market = markets[0]
                print(f"✅ 精确查找成功: {market.get('question')}")
                return market
        
        # 如果精确查找失败，用模糊搜索（更可靠）
        print(f"⚠️ 精确查找失败，尝试搜索最近的市场...")
        markets = search_recent_markets(asset, duration)
        
        if markets:
            # 找出距离当前时间最近且尚未开始的市场
            now_ts = int(time.time())
            for market in markets:
                slug = market.get('slug', '')
                try:
                    time_part = slug.split('-')[-1]
                    if 'T' in time_part:
                        market_time_str = time_part.replace('Z', '')
                        market_ts = int(time.mktime(time.strptime(market_time_str, '%Y-%m-%dT%H:%M')))
                        if market_ts > now_ts:
                            print(f"✅ 找到未开始的市场: {slug}")
                            return market
                except:
                    continue
            
            if markets:
                print(f"⚠️ 未找到未开始市场，返回第一个搜索结果")
                return markets[0]
        
        print(f"❌ 未找到任何 {asset} 的 {duration}分钟市场")
        return None
        
    except Exception as e:
        print(f"❌ 获取市场失败: {e}")
        return None

def search_recent_markets(asset, duration=5, limit=20):
    """搜索最近的5分钟市场"""
    try:
        resp = requests.get(
            f"{CLOB_API}/markets",
            params={
                "limit": limit,
                "next_cursor": ""
            }
        )
        
        if resp.status_code == 200:
            data = resp.json()
            markets = data.get("data", [])
            
            keyword = f"{asset.lower()}-{duration}m"
            filtered = []
            
            for m in markets:
                slug = m.get("slug", "").lower()
                if keyword in slug:
                    filtered.append(m)
            
            if filtered:
                print(f"✅ 模糊搜索到 {len(filtered)} 个相关市场")
                filtered.sort(key=lambda x: x.get('slug', ''), reverse=True)
                return filtered
            
        else:
            print(f"⚠️ 搜索市场失败: HTTP {resp.status_code}")
            
    except Exception as e:
        print(f"❌ 搜索市场异常: {e}")
    
    return []

def get_order_book(condition_id, token_id):
    """获取订单簿，返回最优卖价"""
    try:
        resp = requests.get(
            f"{CLOB_API}/order-book",
            params={
                "condition_id": condition_id,
                "token_id": token_id
            }
        )
        
        if resp.status_code == 200:
            data = resp.json()
            asks = data.get("asks", [])
            if asks and len(asks) > 0:
                price = float(asks[0]["price"])
                print(f"📊 最优卖价: {price}")
                return price
        else:
            print(f"⚠️ 获取订单簿失败: HTTP {resp.status_code}")
            
    except Exception as e:
        print(f"❌ 获取订单簿异常: {e}")
    
    return None

def get_token_id(market_info, side):
    """
    获取 token ID
    side: "UP" 或 "DOWN"
    """
    try:
        tokens = market_info.get("tokens", [])
        if not tokens or len(tokens) < 2:
            print("⚠️ 市场没有足够的 token")
            return None
        
        if side == "UP":
            return tokens[0].get("token_id")
        else:
            return tokens[1].get("token_id")
            
    except Exception as e:
        print(f"❌ 获取 token ID 失败: {e}")
        return None

def place_order(condition_id, token_id, side, amount, price, order_type="limit"):
    """
    下单（模拟版本，实际签名需对接 py_clob_client）
    """
    try:
        print(f"📝 准备下单:")
        print(f"   - 条件ID: {condition_id}")
        print(f"   - Token ID: {token_id}")
        print(f"   - 方向: {side}")
        print(f"   - 数量: {amount} USDC")
        print(f"   - 价格: {price}")
        print(f"   - 类型: {order_type}")
        
        order_id = f"order_{int(time.time())}_{condition_id[:8]}"
        print(f"✅ 订单提交成功: {order_id}")
        
        return {
            "id": order_id,
            "success": True,
            "condition_id": condition_id,
            "token_id": token_id,
            "side": side,
            "amount": amount,
            "price": price,
            "type": order_type,
            "time": int(time.time())
        }
        
    except Exception as e:
        print(f"❌ 下单失败: {e}")
        return None

def get_market_outcome(condition_id):
    """获取市场结果"""
    try:
        resp = requests.get(
            f"{CLOB_API}/markets",
            params={"condition_id": condition_id}
        )
        
        if resp.status_code == 200:
            markets = resp.json()
            if markets and len(markets) > 0:
                market = markets[0]
                if market.get("closed") and market.get("accepted"):
                    outcomes = market.get("outcomes", [])
                    if outcomes and len(outcomes) > 0:
                        return outcomes[0]
        return None
        
    except Exception as e:
        print(f"❌ 获取市场结果失败: {e}")
        return None

def test_connection():
    """测试 API 连接"""
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
