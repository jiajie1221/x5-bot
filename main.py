import time
import requests
import os
from eth_account import Account
from eth_account.messages import encode_defunct

# 从 Railway 的环境变量读取（更安全）
MY_PRIVATE_KEY = os.getenv("MY_PK")
MY_PASSPHRASE = os.getenv("MY_PASS")

def get_creds():
    if not MY_PRIVATE_KEY or not MY_PASSPHRASE:
        print("❌ 错误：请在 Railway 的 Variables 页面添加 MY_PK 和 MY_PASS")
        return

    account = Account.from_key(MY_PRIVATE_KEY)
    host = "https://clob.polymarket.com"
    timestamp = str(int(time.time()))
    
    # 尝试申请
    message = f"{timestamp}POST/auth/api-key"
    signature = account.sign_message(encode_defunct(text=message)).signature.hex()
    
    headers = {
        "POLY_ADDRESS": account.address,
        "POLY_SIGNATURE": signature,
        "POLY_TIMESTAMP": timestamp,
        "Content-Type": "application/json"
    }
    
    print(f"正在为地址 {account.address} 申请 Key...")
    resp = requests.post(f"{host}/auth/api-key", json={"passphrase": MY_PASSPHRASE}, headers=headers)
    
    if resp.status_code in [200, 201]:
        res = resp.json()
        print("\n" + "="*40)
        print("🎉 成功！请复制以下内容：")
        print(f"PK_API_KEY: {res['apiKey']}")
        print(f"PK_API_SECRET: {res['secret']}")
        print(f"PK_API_PASSPHRASE: {MY_PASSPHRASE}")
        print("="*40)
    else:
        print(f"❌ 失败详情: {resp.text}")

if __name__ == "__main__":
    get_creds()
    # 保持运行几分钟，方便你看日志
    time.sleep(300) 
