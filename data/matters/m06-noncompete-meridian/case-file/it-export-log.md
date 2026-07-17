# Exhibit 004 — IT Off-Boarding Export-Log Report

**NORTHLAKE SURGICAL INSTRUMENTS, INC. — INFORMATION SECURITY**
**Off-Boarding Data-Access Review**

**Subject employee:** A. Okwuosa (Sales)
**Review requested:** January 6, 2026
**Prepared by:** André Dupont, IT Security Analyst
**Report date:** January 9, 2026

## Scope

Standard off-boarding review of the subject's workstation activity and network-drive access for the ninety (90) days preceding separation, per company procedure for departing sales personnel.

## Findings

1. **Removable-media event — December 29, 2025, 6:52 p.m.** A file was copied from the Sales shared drive to a removable USB mass-storage device connected to the subject's workstation. The endpoint log records the destination as a USB device and the source file name as `Northlake_Master_Pricing_LakeVerdant_and_Region.xlsx` (approx. 2.1 MB). The log records the copy event and file name; it does not record the subsequent contents or handling of the copied file.

2. **Email forward — December 30, 2025, 8:14 a.m.** One internal email was forwarded from the subject's company mailbox to an external personal address (`adaeze.okwuosa@[personal].com`). The forwarded message was a bid-coordination thread that included, in the signature block of a quoted message, a hospital purchasing contact's direct phone number. No attachment accompanied the forward.

3. **No other flagged exfiltration events.** No bulk downloads, no mass email of customer records, and no access to files outside the subject's normal sales scope were identified in the review window.

## Analyst note

This report reflects what the logs show. It does not, and cannot, establish what was done with the copied file or the forwarded email after the recorded events. Questions about intent or use are outside the scope of a log review.

/s/ André Dupont, IT Security Analyst
