# Rainbow Life Final Deploy

全新 Rainbow Life 單一入口部署版。

## Render

- Build Command: `pip install -r requirements.txt`
- Start Command: `uvicorn main:app --host 0.0.0.0 --port $PORT`
- Health Check: `/health`

## 必要環境變數

- `LINE_CHANNEL_ACCESS_TOKEN`
- `LINE_CHANNEL_SECRET`
- `DATABASE_URL`
- `PUBLIC_BASE_URL`
- `RAINBOW_WEB_SECRET`
- `RAINBOW_OWNER_USER_ID`


## 生活推播中心・第四部分

- 新增 7-ELEVEN 台灣官方活動卡片
- 每 5 分鐘更新一次
- 抓取失敗時顯示「資訊更新中！」
- 保留官方天氣與全家活動
- 麥當勞卡片暫不串接

## 生活推播中心・第五部分
- 新增台灣麥當勞官方活動卡片
- 官方資料每 5 分鐘更新
- 抓取失敗時只顯示「資訊更新中！」
- 保留官方天氣、全家與 7-ELEVEN 活動
