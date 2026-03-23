import email.utils
import mailbox
from datetime import UTC, datetime
from email.mime.base import MIMEBase
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

mbox = mailbox.mbox("tests/fixtures/sample.mbox")

# Message 1: Simple plain text, thread root
msg1 = MIMEText("Hey team, let's discuss the Q1 budget.\n\nThanks,\nAlice")
msg1["Message-ID"] = "<msg001@example.com>"
msg1["From"] = "Alice Smith <alice@example.com>"
msg1["To"] = "bob@example.com, carol@example.com"
msg1["Cc"] = "dave@example.com"
msg1["Subject"] = "Q1 Budget Discussion"
msg1["Date"] = email.utils.format_datetime(datetime(2025, 1, 15, 10, 0, tzinfo=UTC))
mbox.add(msg1)

# Message 2: Reply to msg1 with quoted text
msg2 = MIMEText(
    "Sounds good, I'll prepare the spreadsheet.\n\n> Hey team, let's discuss the Q1 budget.\n> Thanks,\n> Alice\n-- \nBob Jones\nFinance"
)
msg2["Message-ID"] = "<msg002@example.com>"
msg2["From"] = "Bob Jones <bob@example.com>"
msg2["To"] = "alice@example.com"
msg2["Subject"] = "Re: Q1 Budget Discussion"
msg2["Date"] = email.utils.format_datetime(datetime(2025, 1, 15, 14, 30, tzinfo=UTC))
msg2["In-Reply-To"] = "<msg001@example.com>"
msg2["References"] = "<msg001@example.com>"
mbox.add(msg2)

# Message 3: Second reply in thread
msg3 = MIMEText("Can we schedule a meeting for Thursday?")
msg3["Message-ID"] = "<msg003@example.com>"
msg3["From"] = "Carol White <carol@example.com>"
msg3["To"] = "alice@example.com, bob@example.com"
msg3["Subject"] = "Re: Q1 Budget Discussion"
msg3["Date"] = email.utils.format_datetime(datetime(2025, 1, 16, 9, 0, tzinfo=UTC))
msg3["In-Reply-To"] = "<msg002@example.com>"
msg3["References"] = "<msg001@example.com> <msg002@example.com>"
mbox.add(msg3)

# Message 4: HTML-only message, new thread
msg4 = MIMEText("<html><body><h1>Welcome!</h1><p>Your account is ready.</p></body></html>", "html")
msg4["Message-ID"] = "<msg004@notifications.example.com>"
msg4["From"] = "noreply@notifications.example.com"
msg4["To"] = "alice@example.com"
msg4["Subject"] = "Account Ready"
msg4["Date"] = email.utils.format_datetime(datetime(2025, 2, 1, 8, 0, tzinfo=UTC))
mbox.add(msg4)

# Message 5: Multipart with attachment
msg5 = MIMEMultipart()
msg5["Message-ID"] = "<msg005@example.com>"
msg5["From"] = "Dave Miller <dave@example.com>"
msg5["To"] = "alice@example.com"
msg5["Subject"] = "Q1 Report Attached"
msg5["Date"] = email.utils.format_datetime(datetime(2025, 2, 10, 15, 0, tzinfo=UTC))
msg5.attach(MIMEText("Please find the Q1 report attached."))
attachment = MIMEBase("application", "pdf")
attachment.set_payload(b"fake pdf content")
attachment.add_header("Content-Disposition", "attachment", filename="q1-report.pdf")
msg5.attach(attachment)
mbox.add(msg5)

# Message 6: Message with no subject
msg6 = MIMEText("Quick note - the server is down again.")
msg6["Message-ID"] = "<msg006@example.com>"
msg6["From"] = "ops@example.com"
msg6["To"] = "alice@example.com"
msg6["Date"] = email.utils.format_datetime(datetime(2025, 2, 15, 3, 0, tzinfo=UTC))
mbox.add(msg6)

# Message 7: Multiple recipients, BCC
msg7 = MIMEText("Confidential: restructuring plan enclosed.")
msg7["Message-ID"] = "<msg007@example.com>"
msg7["From"] = "CEO <ceo@bigcorp.com>"
msg7["To"] = "alice@example.com"
msg7["Cc"] = "legal@bigcorp.com"
msg7["Bcc"] = "board@bigcorp.com"
msg7["Subject"] = "Confidential - Restructuring"
msg7["Date"] = email.utils.format_datetime(datetime(2025, 3, 1, 12, 0, tzinfo=UTC))
mbox.add(msg7)

# Message 8: Outlook-style quoting
msg8 = MIMEText(
    "I agree with the proposal.\n\n-----Original Message-----\nFrom: someone@example.com\nSent: Monday\nSubject: Proposal\n\nHere is my proposal."
)
msg8["Message-ID"] = "<msg008@example.com>"
msg8["From"] = "Frank <frank@example.com>"
msg8["To"] = "alice@example.com"
msg8["Subject"] = "RE: Proposal"
msg8["Date"] = email.utils.format_datetime(datetime(2025, 3, 5, 16, 0, tzinfo=UTC))
msg8["In-Reply-To"] = "<msg-proposal@example.com>"
mbox.add(msg8)

# Message 9: Timezone-naive date (edge case)
msg9 = MIMEText("Testing timezone handling.")
msg9["Message-ID"] = "<msg009@example.com>"
msg9["From"] = "Grace <grace@example.com>"
msg9["To"] = "alice@example.com"
msg9["Subject"] = "Timezone Test"
msg9["Date"] = "Mon, 10 Mar 2025 10:00:00"  # No timezone
mbox.add(msg9)

# Message 10: Multipart alternative (text + HTML)
msg10 = MIMEMultipart("alternative")
msg10["Message-ID"] = "<msg010@example.com>"
msg10["From"] = "Newsletter <news@updates.example.com>"
msg10["To"] = "alice@example.com"
msg10["Subject"] = "Weekly Update"
msg10["Date"] = email.utils.format_datetime(datetime(2025, 3, 15, 7, 0, tzinfo=UTC))
msg10.attach(MIMEText("This week in tech: AI advances continue."))
msg10.attach(
    MIMEText("<html><body><b>This week in tech:</b> AI advances continue.</body></html>", "html")
)
mbox.add(msg10)

mbox.close()
