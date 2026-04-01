
<!-- PROJECT LOGO -->
<br />
<div align="center">
  <a href="https://github.com/othneildrew/Best-README-Template">
    <img src="images/logo.png" alt="Logo" width="100" height="100">
  </a>

  <h3 align="center">揪霸</h3>

  <p align="center">
    每個主揪都該用的揪團小助手
    <br />
    <a href="https://github.com/YHC2538/Jio-Ba"><strong>Explore the docs »</strong></a>
    <br />
    <br />
    <a href="https://github.com/YHC2538/Jio-Ba">View Demo</a>
    &middot;
    <a href="https://github.com/YHC2538/Jio-Ba/issues">Report Bug</a>
    &middot;
    <a href="https://github.com/YHC2538/Jio-Ba/issues">Request Feature</a>
  </p>
</div>


每次想揪朋友一起出去玩，卻因為要喬時間、地點、做甚麼，嫌麻煩而作罷? 傳統揪團常遇到從眾效應、資訊超載與多方需求難以妥協的問題，所以主揪常常會需要花大把的時間溝通活動參與者的意願，非常沒有效率。
揪霸就因此誕生了!!

以 AI 為核心的 Discord 揪團機器人，主揪以及所有活動參與者，只需要透過簡單的自然語言與揪霸對答，揪霸會將模糊的自然語言轉化為結構化參數，並透過機制設計中的 **Nash Social Welfare (NSW)** 演算法，計算出帕雷托最適 (Pareto Optimal) 的活動方案（包含 What, Where, When），最後由發起人裁決並自動建立 Discord Event。
就能輕鬆敲定活動細節，降低多人協調成本。

[English README](./README.md)



## 使用方式與流程概覽

1. 主揪在伺服器執行 `/jio`。
2. Bot 顯示建立表單，並發出活動卡。
3. 主揪以自然語言填寫活動名稱、條件以及活動說明 (填寫 what,where,when,how)。
4. 揪霸根據未決定的 what,where,when,how，智慧生成對應的訪問問題。
5. 參與者加入後進入面試狀態。
6. Bot 於 DM 先送題目總覽，再逐題訪談。
7. 所以參與者以自然語言回答。
8. 產生候選方案後由主揪定案，揪霸自動公告。



## 💎 揪霸主要功能

- 全自然語言對答。
- 揪霸具有上下文記憶功能。
- 支援報名截止、面試截止、最低成團人數、自訂問題等功能。
- 可根據活動描述產生訪談題目。
- 智慧警告惡意不配合的活動參與者。
- 惡意參與者警告次數達門檻後進入停權狀態 (ON_HOLD)，等待主揪裁決。
- 參與者有 Confirm/Edit 最終確認流程。
- 頻道儀表板即時更新參與者狀態。
- 使用最大化 Nash Social Welfare 演算法，計算出前兩個候選方案讓主揪裁決。

### TODO:
- [ ] 長期紀錄參與者偏好
- [ ] 發起多個活動
- [ ] 修改答案
- [ ] 全 manual 填寫功能



## 📝 目前指令

- `/jio`
   發起活動。
- `/verdict`
   開啟 ON_HOLD 裁決介面。

## ⚓ 架構與模組 (這部分有 outdated, 需要修改)

- `main.py`
   啟動機器人、檢查環境變數、載入 cogs。
- `cogs/jio.py`
   Slash Commands、Discord UI、活動儀表板、流程編排。
- `cogs/ai_brain.py`
   佇列、debounce、呼叫 LangGraph。
- `cogs/graph_agent.py`
   訪談圖節點：gatekeeper、extractor、reprompt、finalize、hold、malicious。
- `cogs/db.py`
   MongoDB CRUD 與狀態遷移。
- `cogs/matching/nsw_calculator.py`
   候選方案評分與排序。

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

## ✈️ 本機啟動

```bash
python -m venv .venv
# Windows PowerShell
.venv\Scripts\Activate.ps1
pip install -r requirements.txt
python main.py
```

## ⛰️ PM2 部署

專案內含 `ecosystem.config.js`：

```bash
pm2 start ecosystem.config.js
pm2 logs
pm2 save
```

## 💡 運維備註

- 需開啟 Message Content Intent 才能完整使用 DM 訪談能力。
- 權限不足時，啟動流程可能切換到 limited mode。

## 💰 Credits

專案原始設計 by rlongdragon