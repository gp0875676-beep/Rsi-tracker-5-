# 📊 Crypto RSI + MFI Live Tracker

Real-time RSI and MFI indicator tracker for any crypto coin.
- **Data:** CoinGecko API (free, no key needed)
- **Timeframes:** 15m, 1H, 4H
- **Auto-refresh:** Every 5 minutes
- **Formula:** Wilder's RSI + Money Flow Index (TradingView-identical)

---

## 🚀 Render pe Deploy Karo (Step by Step)

### Step 1 — GitHub pe upload karo

```bash
git init
git add .
git commit -m "first commit"
git branch -M main
git remote add origin https://github.com/TERA_USERNAME/crypto-tracker.git
git push -u origin main
```

### Step 2 — Render pe jaao

1. [render.com](https://render.com) pe jaao
2. **Sign Up** karo (GitHub se login karo)

### Step 3 — New Web Service banao

1. Dashboard mein **"New +"** click karo
2. **"Web Service"** select karo
3. GitHub repo connect karo → apna `crypto-tracker` repo select karo
4. Yeh settings daalo:
   - **Name:** crypto-rsi-tracker (koi bhi naam)
   - **Runtime:** Python 3
   - **Build Command:** `pip install -r requirements.txt`
   - **Start Command:** `gunicorn app:app`
   - **Instance Type:** Free

5. **"Create Web Service"** click karo

### Step 4 — Done! 🎉

2-3 minute mein deploy ho jayega.
Render ek URL dega jaise: `https://crypto-rsi-tracker.onrender.com`

---

## 💻 Local Test Karne ke Liye

```bash
pip install -r requirements.txt
python app.py
```

Browser mein jaao: `http://localhost:5000`

---

## 📁 Project Structure

```
crypto_tracker/
├── app.py              ← Flask backend + API
├── templates/
│   └── index.html      ← Web dashboard
├── requirements.txt    ← Python dependencies
├── Procfile            ← Gunicorn start command
├── render.yaml         ← Render config
└── .gitignore
```

---

## ⚠️ Notes

- CoinGecko free tier: 10-30 calls/min limit hai
- Free Render instance 15 min baad sleep ho jata hai — pehli baar load thoda slow lagega
- RSI/MFI values TradingView se match karti hain (Wilder's smoothing method)
