<!-- PROJECT LOGO -->
<br />
<div align="center">
  <a href="https://github.com/YHC2538/Jio-Ba">
    <img src="images/logo.png" alt="Logo" width="100" height="100">
  </a>

  <h3 align="center">揪霸 Jio-Ba</h3>

  <p align="center">
    把「揪團很麻煩」變成「一句話就開團」的 Discord AI 代理人
    <br />
    <a href="https://github.com/YHC2538/Jio-Ba"><strong>查看專案 »</strong></a>
    <br />
    <br />
    <a href="https://github.com/YHC2538/Jio-Ba/issues">回報問題</a>
    &middot;
    <a href="https://github.com/YHC2538/Jio-Ba/issues">提出需求</a>
  </p>
</div>

[English README](./README.md)

## 🤖 為什麼是揪霸？

你應該也遇過這種情境：

- 一群人都說「都可以」，最後什麼都喬不成。
- 訊息洗到看不到重點，主揪只能手動整理每個人的需求。
- 不同人的時間、地點偏好衝突，總有一方被迫妥協。

揪霸的定位，不只是「幫你問問題」的機器人，而是 **Discord 裡的活動協調中控台**：

1. 把自然語言需求轉成結構化 4W1H。
2. 用 DM 訪談流程收斂每個人的真實偏好。
3. 將惡意或不配合回覆暫時停權並隔離到 ON_HOLD，交由主揪裁決。
4. 最後用 Nash Social Welfare (NSW) 產生候選方案，讓主揪快速定案。

## 📢 核心能力

- 活動建立與管理（標題、說明、截止時間、最低成團人數、自訂題目）。
- 自動抽取與展示 4W1H seeds（What/Where/When/How，必要時含 Why）。
- AI 動態生成訪談題目（依缺失資訊補題，而不是固定問卷）。
- DM 訪談流程（逐題、追問、確認送出）。
- 對付惡意參與者（warning、ON_HOLD、主揪裁決 CONTINUE/KICK）。
- 頻道儀表板即時更新（Joined/Interviewing/ON_HOLD/Ready/Kicked）。
- 最終方案公告與 Discord Scheduled Event 串接。

## 🔑 使用流程（Host 視角）

1. 在伺服器輸入 `/jio`。
2. 填寫活動資訊，送出後產生活動卡與 Join 按鈕。
3. 參與者加入後，報名截止或主揪提早結束報名，系統啟動 DM 面試。
4. 面試完成後，系統產生幕僚報告與候選方案給主揪。
5. 主揪按下裁決方案，系統公告最終結果並結束流程。

## 📜 目前可用指令

- `/jio`
  發起活動（可帶入標題與時間限制等參數）。
- `/verdict`
  開啟 ON_HOLD 成員裁決介面。

## ⚓ 架構與模組

採用 LangChain 以及 LangGraph 構建 AI Agent 的工作流程

- `main.py`
  Bot 啟動入口、環境檢查、載入 cogs。
- `cogs/jio.py`
  Slash Commands、Discord UI、活動流程協調、儀表板更新。
- `cogs/ai_brain.py`
  參與者訊息佇列、debounce、LangGraph 呼叫與回寫狀態。
- `cogs/graph_agent.py`
  訪談圖節點（analyze / reprompt / next_question / malicious / hold / finalize）。
- `cogs/db.py`
  MongoDB 存取、活動與參與者狀態遷移。
- `cogs/matching/nsw_calculator.py`
  NSW 候選方案評分與排序。

## 📌 環境需求

- Python `>=3.10,<3.11`
- MongoDB
- Discord Bot Token
- Google API Key（Gemini）

## 📁 環境變數

請在 `.env` 設定：

```env
DISCORD_TOKEN=你的_discord_token
MONGO_URI=你的_mongodb_uri
GOOGLE_API_KEY=你的_google_api_key
GOOGLE_API_ENDPOINT=https://generativelanguage.googleapis.com
```

## ✈️ 本機安裝

### 使用 pip

```bash
pip install .
python main.py
```


### 使用 uv

```bash
# 1) 安裝相依套件（依 pyproject.toml，若有 uv.lock 會一併採用）
uv sync

# 2) 啟動 Bot
uv run python main.py
```

## ⛰️ PM2 部署

專案內含 `ecosystem.config.js`，可依部署環境調整 Python interpreter 路徑後啟用：

```bash
pm2 start ecosystem.config.js
pm2 logs
pm2 save
```

## 💡 運維備註

- 請在 Discord Developer Portal 開啟 Message Content Intent，否則 DM 訪談能力會受限。
- 若權限不足，系統可能進入 limited mode。
- `/jio` 需在伺服器頻道執行，非 DM。

## 💰 Credits

專案原始設計 by rlongdragon