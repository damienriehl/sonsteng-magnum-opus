# Witness Statement — André Dupont {#b:0f34789c}

**Witness:** André Dupont, IT Security Analyst, Northlake Surgical Instruments
**Context:** Statement given in connection with the off-boarding review he prepared (Exhibit 004)
**Date:** April 30, 2026 {#b:2ed0f4f4}

My name is André Dupont. I am an information-security analyst at Northlake Surgical Instruments. Part of my job is running off-boarding data-access reviews when an employee in a sensitive role leaves the company. I ran the review on Ms. Okwuosa's workstation and mailbox after her separation, and I prepared the export-log report. {#b:939be372}

I want to be careful about what I can and cannot say, because I think that distinction matters. What I can tell you is what the logs record. On the evening of December 29, 2025, our endpoint log shows a file being copied from the Sales shared drive to a USB storage device connected to Ms. Okwuosa's workstation. The source file name in the log matches the master pricing workbook, and the size is about 2.1 megabytes. The next morning, December 30, the mail log shows one internal email forwarded from her company mailbox to an external personal address. That email was a bid-coordination thread; buried in a quoted signature block was a hospital contact's direct phone number. There was no attachment on the forward. {#b:faf4aa09}

That is the entirety of what the logs establish. I want to be clear about the limits. The log records that a copy event happened and the name of the file. It does not record what was done with the file afterward — whether it was ever opened again, whether it was deleted, whether it was shown to anyone, or whether it was used for anything at all. A USB copy event is a USB copy event. I have no technical basis to say what her intent was, and I would not testify to it. {#b:b30e2231}

I also did not find any bulk exfiltration. There was no mass download of records, no mass email of customer data, and no access to systems outside her normal sales work. If someone were systematically stealing a customer database, I would generally expect to see a very different pattern. I saw two events: one file copy and one email forward. {#b:790ba0f2}

I was asked whether the pricing file was technically protected. On the file itself, no — there was no file-level password or encryption. Access was governed by the general network login, and the Sales drive was open to the sales department. That is what our access-control records show. {#b:eca8183a}

I am providing this statement to describe my report accurately and to make sure my findings are not stretched beyond what the logs actually say. {#b:b87f1004}

/s/ André Dupont {#b:91dbf9d3}
