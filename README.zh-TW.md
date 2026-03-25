# Wei-Jia-Ba (未呷飽) Discord Bot

這是一個聰明的 Discord 機器人，協助群組決定聚餐地點。它支援 AI 分析、隨機抽選或獨裁模式。

[English Version](./README.md)

## 功能特色

- **發起聚餐**: 使用 `/jio` 指令快速建立聚餐活動。
- **決策模式**:
  - 🤖 **AI**: 使用大型語言模型分析大家偏好並推薦餐廳。
  - 🎲 **隨機**: 隨機選出一位參加者的提議。
  - 👑 **獨裁**: 由發起人直接決定。
- **互動介面**: 使用 Discord 按鈕與表單 (Modals) 進行報名與填寫偏好。
- **智慧協調**: AI 會擔任協調者，主動詢問參加者以釐清模糊的偏好 (例如：「隨便」是什麼意思？)。

## 事前準備

- Python 3.10 或以上版本
- MongoDB 資料庫
- Discord Bot Token
- OpenRouter API Key
- Google Gemini API Key

## 安裝教學

1. **複製專案**
   ```bash
   git clone <repository_url>
   cd <repository_name>
   ```

2. **安裝 Python 套件**
   ```bash
   pip install -r requirements.txt
   ```

## 設定說明

1. **設定環境變數**
   複製範例設定檔：
   ```bash
   cp .env.example .env
   ```

2. **編輯 `.env`**
   填入您的 API Keys 與相關設定：
   ```ini
   DISCORD_TOKEN=您的_discord_token
   MONGO_URI=您的_mongo_db_連線字串
   OPENROUTER_API_KEY=您的_openrouter_key
   OPENROUTER_API_ENDPOINT=https://openrouter.ai/api/v1
   GOOGLE_API_KEY=您的_google_gemini_key
   GOOGLE_API_ENDPOINT=https://generativelanguage.googleapis.com
   ```

## 啟動機器人

### 本機開發
```bash
python main.py
```

### 正式部屬 (PM2)
本專案包含 PM2 設定檔 `ecosystem.config.js`。

```bash
# 啟動機器人
pm2 start ecosystem.config.js

# 查看日誌
pm2 logs discord-bot

# 儲存行程以便開機自動啟動
pm2 save
pm2 startup
```

## 使用說明

### 指令列表

- **`/jio`**: 發起一個新的聚餐活動。
  - 可選參數: `title` (標題), `time_limit` (時間限制), `decision_mode` (決策模式)。
- **`/export_status`**: 匯出目前活動狀態為 JSON 檔案。

### 運作流程

1. 使用者輸入 `/jio` 建立活動。
2. Bot 發送包含「參加」按鈕的訊息卡片。
3. 參加者點擊按鈕並填寫偏好 (例如：「拉麵」、「不要辣」)。
4. **AI 模式**:
   - Bot 可能會私訊參加者以釐清偏好。
   - 當所有人準備好或時間截止，AI 將推薦餐廳。
5. **面試階段**:
   - 當活動截止 (手動或自動)，Bot 進入面試階段以最終確認餐廳。

## 授權

[MIT](LICENSE)
