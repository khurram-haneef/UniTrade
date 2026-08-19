"""Server-side email delivery for verification OTPs.

SMTP values come exclusively from Streamlit Secrets or environment variables.
No credential is stored in the user database or shown to users.
"""
import smtplib
from email.message import EmailMessage


class EmailDeliveryError(RuntimeError):
    pass


class SmtpEmailService:
    def __init__(self, host=None, port=None, username=None, password=None, sender=None, use_tls=True):
        self.host = host
        self.port = int(port or 587)
        self.username = username
        self.password = password
        self.sender = sender
        self.use_tls = str(use_tls).lower() not in {"false", "0", "no"}

    @property
    def configured(self):
        return bool(self.host and self.username and self.password and self.sender)

    def send_verification_otp(self, recipient: str, code: str):
        if not self.configured:
            raise EmailDeliveryError("Email delivery is not configured on this server.")
        message = EmailMessage()
        message["Subject"] = "UniTrade email verification code"
        message["From"] = self.sender
        message["To"] = recipient
        message.set_content(
            f"Your UniTrade verification code is: {code}\n\n"
            "This code expires in 15 minutes. Do not share it with anyone."
        )
        try:
            with smtplib.SMTP(self.host, self.port, timeout=20) as smtp:
                if self.use_tls:
                    smtp.starttls()
                smtp.login(self.username, self.password)
                smtp.send_message(message)
        except (OSError, smtplib.SMTPException) as exc:
            raise EmailDeliveryError("Could not send the verification email. Please try again later.") from exc
