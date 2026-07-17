# IT Off-Boarding Forensic Review and Device-Return Receipt — Exhibit m16.exh.006

**HUDSON VALLEY BIOMETRICS, INC. — Information Security**
**Off-Boarding Review Report**

**Departing Employee:** Priya Iyer
**Last Day:** January 30, 2026
**Review Period:** January 16 – January 31, 2026
**Prepared by:** T. Nagata, IT Security Analyst
**Report Date:** February 4, 2026

---

## 1. Devices returned

| Item | Serial / Asset Tag | Condition | Date returned |
|---|---|---|---|
| Laptop (company-issued) | HVB-LT-2291 | Intact; wiped after imaging | Jan 30, 2026 |
| Mobile phone (company-issued) | HVB-PH-0884 | Intact | Jan 30, 2026 |
| Access badge | BADGE-3391 | Deactivated | Jan 30, 2026 |

All company-issued devices were returned on the employee's last day. No devices are outstanding.

## 2. Scope of review

Per standard practice for departing engineering staff, Information Security imaged the returned laptop and reviewed: (a) the employee's company email account for external-forwarding activity in the 60 days before departure; (b) company cloud-storage and code-repository access logs; (c) removable-media (USB) connection logs on the laptop; and (d) any personal cloud accounts the employee had linked to company systems.

## 3. Findings

- **Source code / repositories.** No evidence that company source code, model files, or training data were copied to any removable media, personal cloud account, or personal email. USB connection logs show no mass-storage device attached in the review period.
- **Email forwarding.** One outbound message from the employee's company account to her personal email address was identified, dated January 27, 2026, with an attached compressed folder. Security review of the attachment identified it as containing personal files (résumé drafts, conference presentation slides) and one hand-drawn architecture diagram. The diagram is not source code and not a controlled document; its status is noted for management's attention.
- **Customer data.** No customer records or contact files were found to have been exported.
- **Bulk download.** No anomalous bulk-download activity was detected in the review period.

## 4. Analyst note

With the exception of the single personal-folder email noted in Section 3, this review found no indication that company code, data, or customer information was exfiltrated. The forwarded folder has been preserved. This report states facts observed in the logs and images; it does not opine on any legal question.

/s/ T. Nagata, IT Security Analyst
