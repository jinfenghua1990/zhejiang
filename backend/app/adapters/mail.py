import smtplib
from email.header import Header
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.utils import formataddr

from app.adapters.base import AdapterNotConfigured
from app.config import settings

"""MailAdapter —— 财务邮件交付（规格 10）。未配置 SMTP 时明确抛出，不发送测试邮件。"""


class MailAdapter:
    provider = "smtp"

    def ensure_configured(self) -> None:
        if not settings.smtp_configured:
            raise AdapterNotConfigured("SMTP 未配置（等待邮件服务与收件人信息）")

    def send(self, subject: str, body: str, to_addrs: list[str], cc_addrs: list[str] | None = None,
             attachments: list[str] | None = None) -> str:
        self.ensure_configured()
        msg = MIMEMultipart()
        msg["Subject"] = Header(subject, "utf-8")
        msg["From"] = formataddr((str(Header(settings.APP_NAME, "utf-8")), settings.SMTP_FROM))
        msg["To"] = ",".join(to_addrs)
        if cc_addrs:
            msg["Cc"] = ",".join(cc_addrs)
        msg.attach(MIMEText(body, "plain", "utf-8"))

        for path in attachments or []:
            from email.mime.application import MIMEApplication

            with open(path, "rb") as f:
                part = MIMEApplication(f.read())
            part.add_header("Content-Disposition", "attachment",
                            filename=Header(path.split("/")[-1], "utf-8").encode())
            msg.attach(part)

        port = settings.SMTP_PORT
        if port == 465:
            client = smtplib.SMTP_SSL(settings.SMTP_HOST, port, timeout=30)
        else:
            client = smtplib.SMTP(settings.SMTP_HOST, port, timeout=30)
            client.starttls()
        try:
            client.login(settings.SMTP_USERNAME, settings.SMTP_PASSWORD)
            recv = list(to_addrs) + list(cc_addrs or [])
            send_err = client.sendmail(settings.SMTP_FROM, recv, msg.as_string())
            message_id = msg.get("Message-ID", "")
            _ = send_err
            return message_id
        finally:
            client.quit()
