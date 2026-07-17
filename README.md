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
