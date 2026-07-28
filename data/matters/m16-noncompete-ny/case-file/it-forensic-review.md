# IT Off-Boarding Forensic Review and Device-Return Receipt — Exhibit m16.exh.006 {#b:42a2ef74}

**HUDSON VALLEY BIOMETRICS, INC. — Information Security**
**Off-Boarding Review Report** {#b:944bbaa2}

**Departing Employee:** Priya Iyer
**Last Day:** January 30, 2026
**Review Period:** January 16 – January 31, 2026
**Prepared by:** T. Nagata, IT Security Analyst
**Report Date:** February 4, 2026 {#b:6b5934b9}

---

## 1. Devices returned {#b:dd82ff72}

| Item | Serial / Asset Tag | Condition | Date returned |
|---|---|---|---|
| Laptop (company-issued) | HVB-LT-2291 | Intact; wiped after imaging | Jan 30, 2026 |
| Mobile phone (company-issued) | HVB-PH-0884 | Intact | Jan 30, 2026 |
| Access badge | BADGE-3391 | Deactivated | Jan 30, 2026 |

All company-issued devices were returned on the employee's last day. No devices are outstanding. {#b:6b2b167c}

## 2. Scope of review {#b:781e34e7}

Per standard practice for departing engineering staff, Information Security imaged the returned laptop and reviewed: (a) the employee's company email account for external-forwarding activity in the 60 days before departure; (b) company cloud-storage and code-repository access logs; (c) removable-media (USB) connection logs on the laptop; and (d) any personal cloud accounts the employee had linked to company systems. {#b:ab7c150b}

## 3. Findings {#b:422ded9c}

- **Source code / repositories.** No evidence that company source code, model files, or training data were copied to any removable media, personal cloud account, or personal email. USB connection logs show no mass-storage device attached in the review period. {#b:d72eed0c}
- **Email forwarding.** One outbound message from the employee's company account to her personal email address was identified, dated January 27, 2026, with an attached compressed folder. Security review of the attachment identified it as containing personal files (résumé drafts, conference presentation slides) and one hand-drawn architecture diagram. The diagram is not source code and not a controlled document; its status is noted for management's attention. {#b:32e40d6e}
- **Customer data.** No customer records or contact files were found to have been exported. {#b:0deb72f9}
- **Bulk download.** No anomalous bulk-download activity was detected in the review period. {#b:f86ae510}

## 4. Analyst note {#b:b2103a25}

With the exception of the single personal-folder email noted in Section 3, this review found no indication that company code, data, or customer information was exfiltrated. The forwarded folder has been preserved. This report states facts observed in the logs and images; it does not opine on any legal question. {#b:da42ed6b}

/s/ T. Nagata, IT Security Analyst {#b:c613b3d6}
