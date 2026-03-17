import sqlite3

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
    print(f"✅ 已保存 {market} 回合状态：阶段 {data['stage']}，状态 {data['status']}")

def delete_round(market):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("DELETE FROM rounds WHERE market=?", (market,))
    conn.commit()
    conn.close()
    print(f"✅ 已删除 {market} 回合记录")
