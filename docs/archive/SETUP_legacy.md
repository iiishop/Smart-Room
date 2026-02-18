# Quick Setup Guide

## 1. Get Home Assistant Access Token

1. Open Home Assistant in your browser (e.g., http://homeassistant.local:8123)
2. Click your **profile icon** in the bottom left corner
3. Scroll down to **"Long-Lived Access Tokens"** section
4. Click **"Create Token"**
5. Give it a name like "Smart Room Integration"
6. **Copy the token** immediately (it's only shown once!)

## 2. Configure the Application

Create a `.env` file in the project root:

```bash
cp .env.example .env
```

Edit `.env` and paste your token:

```env
HA_URL=http://homeassistant.local:8123
HA_ACCESS_TOKEN=paste_your_token_here
```

If your Home Assistant is on a different URL, update `HA_URL` accordingly:
- Local: `http://homeassistant.local:8123`
- IP address: `http://192.168.1.100:8123`
- Remote: `https://your-domain.duckdns.org`

## 3. Install Dependencies

```bash
cd backend
pip install -r requirements.txt
```

Or with uv:
```bash
cd backend
uv pip install -r requirements.txt
```

## 4. Start the Application

```bash
cd backend
python main.py
```

You should see:
```
INFO:     Started server process
INFO:     Waiting for application startup.
INFO:     Connected to Home Assistant: Home
INFO:     Discovering devices from Home Assistant...
INFO:     Converted X devices from Home Assistant
INFO:     Application startup complete.
INFO:     Uvicorn running on http://0.0.0.0:8000
```

## 5. Open the Web Interface

Open your browser and go to:
```
http://localhost:8000
```

You should see:
- **System Status**: "running"
- **Active Devices**: Number of discovered devices
- **Home Assistant**: "Connected"

All your Home Assistant devices should appear as cards!

## 6. Test Device Control

Try controlling a device:
1. Find a light or switch in the interface
2. Toggle the power switch
3. The device should respond in real life!
4. Check Home Assistant interface - the state should also update there

## Troubleshooting

### "Failed to connect to Home Assistant"

**Check 1:** Can you access Home Assistant in your browser?
- Try opening: http://homeassistant.local:8123
- If not accessible, Home Assistant might be down

**Check 2:** Is your token correct?
- Make sure you copied the entire token
- No extra spaces before/after the token
- Token should be very long (100+ characters)

**Check 3:** Is the URL correct?
- If you access HA via IP, use: `HA_URL=http://192.168.1.100:8123`
- If you use HTTPS, use: `HA_URL=https://your-domain.com`

### "No devices showing"

**Check 1:** Does Home Assistant have devices?
- Open Home Assistant
- Go to Settings → Devices & Services
- Make sure you have devices configured

**Check 2:** Are devices in supported domains?
- We support: light, switch, sensor, climate, fan, cover, media_player, lock
- Other entity types might not show up yet

**Check 3:** Try manual scan
- Click "Scan Network" button in the interface
- Check browser console for errors (F12)

### "Cannot control device"

**Check 1:** Can you control it in Home Assistant?
- Try controlling the device directly in Home Assistant
- If it doesn't work there, the problem is with the device

**Check 2:** Check backend logs
- Look at terminal where you ran `python main.py`
- You should see error messages if control fails

**Check 3:** Refresh device state
- Click "Refresh Devices" button
- Wait a few seconds and try again

### CORS Errors in Browser

Don't open `index.html` directly (file://)!

Instead:
1. Start the backend: `python main.py`
2. Open: http://localhost:8000

The backend serves the frontend automatically.

### Port 8000 Already in Use

If port 8000 is busy, edit `.env`:

```env
API_PORT=8001
```

Then access via: http://localhost:8001

## Common Questions

**Q: Do I need to keep Home Assistant running?**  
A: Yes! This system is a frontend for Home Assistant. Home Assistant must be running for it to work.

**Q: Will this replace Home Assistant?**  
A: No, it's an alternative interface. All device control goes through Home Assistant.

**Q: Can I use this remotely?**  
A: Yes, but you need to:
1. Expose Home Assistant remotely (use Nabu Casa or DuckDNS)
2. Update `HA_URL` to your remote URL
3. Expose your backend (port 8000) or run it on a server

**Q: Why use this instead of Home Assistant's interface?**  
A: This provides a simpler, more focused interface for device management. Good for:
- Academic demonstrations
- Kiosk displays
- Simplified control panels
- Learning about IoT systems

**Q: Can I add new device types?**  
A: Yes! Edit `backend/adapters/homeassistant/ha_converter.py` to add support for more Home Assistant entity types.

## Next Steps

- Add more devices to Home Assistant
- Organize devices by room/area
- Explore the API endpoints: http://localhost:8000/docs
- Customize the frontend styling
- Add automation rules in Home Assistant

## Need Help?

Check the main README.md for:
- Full API documentation
- Architecture details
- Development guide
- Advanced configuration

Enjoy your Smart Room! 🏠✨
