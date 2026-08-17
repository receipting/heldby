import smtplib
from email.message import EmailMessage

import mailchimp_marketing

PROCESS: str = "contact-extraction"
mc = mailchimp_marketing.Client()


def subscribe(list_id: str, chosen: str):
    # alter-recipients, audience: the model's extracted address joins the list.
    return mc.lists.add_list_member(list_id, {"email_address": chosen})


def notify(chosen: str, body: str):
    msg = EmailMessage()
    msg.set_content(body)
    # alter-recipients, header: add_header carries the address the model chose.
    msg.add_header("To", chosen)
    msg.add_header("Content-Type", "text/plain")  # not a recipient — must stay quiet
    with smtplib.SMTP("localhost") as s:
        s.send_message(msg)
