# Backend — Alexa Voice Assistant

A small browser-based frontend and Python backend based on the supplied Alexa command script. It supports voice or typed commands for Google Classroom, YouTube, web search, Google, Claude, Settings, Gemini, Drive, GitHub, WhatsApp, Gmail, ChatGPT, and Instagram.

## Run it

Python 3.10 or newer is all the backend needs; it uses the standard library.

```bash
python backend.py
```

Then open [http://localhost:8000](http://localhost:8000). To change the port, run `python backend.py --port 8080` (or set `PORT`). For a terminal-only version, run `python backend.py --cli`.

## Voice and links

- Voice capture and spoken replies use your browser's speech APIs. Chrome or Edge on `localhost` or HTTPS is recommended. If voice capture is unavailable, typed commands still work.
- Depending on your browser, speech recognition may use the browser vendor's service. Typed commands are sent to this local backend.
- The backend returns a safe action link; click **Open …** in Alexa's reply to open the requested site in a new tab.
- Device Settings cannot be opened from a browser, so that command explains the limitation.

## Tests

```bash
python -m unittest discover -s tests -v
```

The initial repository did not include the referenced PDF, so this UI follows the supplied Alexa script rather than matching a PDF pixel-for-pixel. Provide the PDF to tune the layout to that reference.
