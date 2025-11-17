import os
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from typing import Any, Dict, Optional
import logging
import json
import requests

from database import create_document
from schemas import Contactlead

app = FastAPI()

# Basic logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("app")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

@app.get("/")
def read_root():
    return {"message": "Hello from FastAPI Backend!"}

@app.get("/api/hello")
def hello():
    return {"message": "Hello from the backend API!"}

@app.get("/test")
def test_database():
    """Test endpoint to check if database is available and accessible"""
    response: Dict[str, Any] = {
        "backend": "✅ Running",
        "database": "❌ Not Available",
        "database_url": None,
        "database_name": None,
        "connection_status": "Not Connected",
        "collections": []
    }
    
    try:
        # Try to import database module
        from database import db
        
        if db is not None:
            response["database"] = "✅ Available"
            response["database_url"] = "✅ Configured"
            response["database_name"] = db.name if hasattr(db, 'name') else "✅ Connected"
            response["connection_status"] = "Connected"
            
            # Try to list collections to verify connectivity
            try:
                collections = db.list_collection_names()
                response["collections"] = collections[:10]  # Show first 10 collections
                response["database"] = "✅ Connected & Working"
            except Exception as e:
                response["database"] = f"⚠️  Connected but Error: {str(e)[:50]}"
        else:
            response["database"] = "⚠️  Available but not initialized"
            
    except ImportError:
        response["database"] = "❌ Database module not found (run enable-database first)"
    except Exception as e:
        response["database"] = f"❌ Error: {str(e)[:50]}"
    
    # Check environment variables
    import os
    response["database_url"] = "✅ Set" if os.getenv("DATABASE_URL") else "❌ Not Set"
    response["database_name"] = "✅ Set" if os.getenv("DATABASE_NAME") else "❌ Not Set"
    
    return response


def send_email_via_sendgrid(to_email: str, subject: str, html_content: str) -> bool:
    """Send email using SendGrid's REST API via requests (no extra deps)."""
    api_key = os.getenv("SENDGRID_API_KEY")
    if not api_key:
        return False

    from_email = os.getenv("EMAIL_FROM", "noreply@neonlabs.app")
    url = "https://api.sendgrid.com/v3/mail/send"
    payload = {
        "personalizations": [{"to": [{"email": to_email}]}],
        "from": {"email": from_email, "name": os.getenv("EMAIL_FROM_NAME", "Neon Labs")},
        "subject": subject,
        "content": [{"type": "text/html", "value": html_content}],
    }
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    try:
        resp = requests.post(url, headers=headers, data=json.dumps(payload), timeout=10)
        if resp.status_code in (200, 202):
            logger.info("SendGrid email accepted for delivery")
            return True
        logger.warning("SendGrid email failed: %s %s", resp.status_code, resp.text[:200])
        return False
    except Exception as e:
        logger.exception("Error sending email via SendGrid: %s", e)
        return False


def send_email_via_smtp(to_email: str, subject: str, html_content: str) -> bool:
    """Fallback SMTP sending if SMTP_* env vars are provided."""
    import smtplib
    from email.mime.text import MIMEText
    from email.mime.multipart import MIMEMultipart

    host = os.getenv("SMTP_HOST")
    port = int(os.getenv("SMTP_PORT", "587"))
    user = os.getenv("SMTP_USER")
    password = os.getenv("SMTP_PASS")
    use_tls = os.getenv("SMTP_STARTTLS", "true").lower() == "true"
    from_email = os.getenv("EMAIL_FROM", user or "noreply@neonlabs.app")

    if not host or not user or not password:
        return False

    try:
        msg = MIMEMultipart("alternative")
        msg["Subject"] = subject
        msg["From"] = from_email
        msg["To"] = to_email
        msg.attach(MIMEText(html_content, "html"))

        server = smtplib.SMTP(host, port, timeout=15)
        if use_tls:
            server.starttls()
        server.login(user, password)
        server.sendmail(from_email, [to_email], msg.as_string())
        server.quit()
        logger.info("SMTP email sent successfully")
        return True
    except Exception as e:
        logger.exception("Error sending email via SMTP: %s", e)
        return False


def send_notification(to_email: str, subject: str, html: str) -> bool:
    """Try SendGrid first, then SMTP."""
    if os.getenv("SENDGRID_API_KEY"):
        ok = send_email_via_sendgrid(to_email, subject, html)
        if ok:
            return True
    if os.getenv("SMTP_HOST") and os.getenv("SMTP_USER") and os.getenv("SMTP_PASS"):
        return send_email_via_smtp(to_email, subject, html)
    logger.warning("No email provider configured. Set SENDGRID_API_KEY or SMTP_* env vars to enable emails.")
    return False


class EmailTest(BaseModel):
    to: Optional[str] = None


@app.post("/api/email/test")
def email_test(body: EmailTest):
    """Send a test email to verify provider configuration."""
    to_email = body.to or os.getenv("EMAIL_TO", "arslan.rai2662@gmail.com")
    subject = "Test: Email configuration"
    html = """
    <h2>Email Test</h2>
    <p>If you're seeing this, your email provider is configured correctly.</p>
    <p>Source: Backend test endpoint.</p>
    """
    try:
        ok = send_notification(to_email, subject, html)
        return {"ok": ok, "to": to_email}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/contact")
def create_contact_lead(payload: Contactlead):
    """Capture contact form submissions and persist to MongoDB; then email notification."""
    try:
        lead_dict = payload.model_dump()
        inserted_id = create_document("contactlead", lead_dict)

        # Email notification
        to_email = os.getenv("EMAIL_TO", "arslan.rai2662@gmail.com")
        subject = f"New Website Lead: {lead_dict.get('name')}"
        html = f"""
        <h2>New Contact Lead</h2>
        <p><strong>Name:</strong> {lead_dict.get('name')}</p>
        <p><strong>Email:</strong> {lead_dict.get('email')}</p>
        <p><strong>Message:</strong><br/>{(lead_dict.get('message') or '').replace('\n','<br/>')}</p>
        <p><strong>Source:</strong> {lead_dict.get('source') or 'website'}</p>
        <p><em>Lead ID:</em> {inserted_id}</p>
        """

        email_sent = send_notification(to_email, subject, html)

        return {"status": "ok", "id": inserted_id, "email_sent": email_sent}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


if __name__ == "__main__":
    import uvicorn
    port = int(os.getenv("PORT", 8000))
    uvicorn.run(app, host="0.0.0.0", port=port)
