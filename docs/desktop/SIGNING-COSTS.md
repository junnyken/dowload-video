# Code-signing cost sheet (Windows now, Apple for C4)

**Tóm tắt cho chủ dự án (VI).** Bảng chi phí ký mã (code signing) cho app desktop. Tất cả giá dưới đây được đọc từ trang của nhà cung cấp vào ngày **2026-10-06**, kèm URL; thứ gì không xác nhận được từ nguồn chính thì ghi `unverified`. Điểm cần chú ý: (1) Azure Artifact Signing (tên cũ Trusted Signing) rẻ nhất (9,99 USD/tháng) nhưng Microsoft chỉ cấp chứng thư công khai cho **tổ chức** ở một danh sách nước (Việt Nam **không có** trong danh sách tôi đọc được) và cá nhân chỉ ở Mỹ/Canada, và dịch vụ **không phát hành EV**; (2) SSL.com OV 129 USD/năm, EV 349 USD/năm, cộng thêm phí chữ ký đám mây eSigner hoặc token YubiKey; (3) Apple Developer 99 USD/năm (chỉ cần ở giai đoạn C4). Các con số cộng gộp bên dưới là phép tính số học của tôi, không phải giá niêm yết. Chưa có giá nào được kiểm tra từ nhà cung cấp khác (DigiCert, Sectigo…), nên ghi `unverified`/chưa tra.

Currency USD. Prices exclude tax/VAT and any currency conversion; vendor pages say prices may vary by agreement/region. Date read: **2026-10-06** for every row.

## 1. Windows — options and prices

### 1.1 Azure Artifact Signing (formerly Trusted Signing) — cloud, certificate never leaves Microsoft

| Item | Value | Source (read 2026-10-06) |
|---|---|---|
| Basic | 9.99 USD/month, up to 5,000 signatures; 0.005 USD per additional signature; 1 certificate profile of each type | https://azure.microsoft.com/en-us/products/artifact-signing |
| Premium | 99.99 USD/month, up to 100,000 signatures; 0.005 USD per additional signature; 10 profiles of each type | same |
| Billing note | Not pro-rated: full SKU amount invoiced regardless of when you start using it | https://learn.microsoft.com/en-us/azure/artifact-signing/faq |
| Needs | A **paid** Azure subscription (free/trial/sponsored not supported) | same FAQ |
| Detail pricing page | https://azure.microsoft.com/en-us/pricing/details/artifact-signing/ rendered the price cells as placeholders when fetched ("$-") and says prices are estimates, so the product page above is the only place I saw the numbers. Treat as **confirmed on one page, re-check in the Azure portal before purchase** | — |

Eligibility (quoted from https://learn.microsoft.com/en-us/azure/artifact-signing/quickstart, page updated 2026-09-29 per its metadata):
- Public Trust certificates are available to **organizations** in the United States, Canada, the European Union, the United Kingdom, Australia, New Zealand, Japan, South Korea, Singapore, Switzerland, Norway and Israel.
- **Individual developers must be located in the United States or Canada.**
- Geographic restrictions do not apply to Private Trust certificates (those are not publicly trusted on end-user machines, so they do not help SmartScreen for public downloads `[inference — confirm with Microsoft]`).
- Identity validation for an organization takes 1 to 20 business days, possibly longer; individuals use an ID-verification flow (quickstart page).
- Account regions listed include several US regions, Brazil South, Japan East, Korea Central, North/West Europe, Poland Central, Switzerland North (same quickstart).
- **Does not issue EV certificates and has no plan to** (FAQ above).
- Vietnam is **not in the country list** that I read. If the certificate-holding legal entity is Vietnamese this option is likely unavailable; owner to confirm the entity's country and whether an eligible-country entity exists. `[open decision]`
- SmartScreen: the FAQ says reputation builds automatically as the signed file gets download history; a prompt may continue to appear until then. Signing does not guarantee an instant clean SmartScreen.

### 1.2 SSL.com certificates (OV / EV), per year

Source: https://www.ssl.com/products/software-integrity/code-signing/ov/ and https://www.ssl.com/products/software-integrity/code-signing/ev/ (read 2026-10-06).

| Type | 1 yr | 2 yr (per yr) | 3 yr (per yr) | 4 yr (per yr) | 5 yr (per yr) |
|---|---|---|---|---|---|
| OV | 129.00 | 116.10 | 109.65 | 103.20 | 96.75 |
| EV | 349.00 | 299.00 | 249.00 | 200.00 | 149.00 |

Other items on the same pages:
- Expedited validation: +599.00 USD (2 business days) vs standard 3–5 days included.
- YubiKey hardware token: 379.00 USD each (listed on both pages). The EV page says SSL.com requires EV private keys to be in a FIPS 140-2 Level 2 validated HSM.
- Cloud HSM attestation options listed (AWS CloudHSM 1,500; Google Cloud HSM 500; Azure Dedicated HSM 500) — not relevant for us; noted for completeness.
- EV Sole Proprietor / IV: the eSigner page says certificates range "129–359 USD annually depending on validation level (IV, OV, EV, EV Sole Proprietor)" (https://www.ssl.com/products/software-integrity/signing-service/). A 359.00 USD figure for EV Sole Proprietor appeared only in a search-result snippet; I did not open that product page → **unverified**.
- This matches the plan's "roughly 129–349 USD" range for Windows signing (the plan's range is OV–EV at SSL.com 1-year list price).

### 1.3 SSL.com eSigner cloud signing (use the certificate without a hardware token)

Source: https://www.ssl.com/products/software-integrity/signing-service/ (read 2026-10-06). The page states these subscriptions are **in addition to** the certificate price.

| Tier | Price/month | Signings included | Credentials |
|---|---|---|---|
| 1 | 15.00 | 240 | 1 |
| 2 | 63.75 | 1,200 | 5 |
| 3 | 131.25 | 3,600 | 9 |
| 4 | 187.50 | 12,000 | 13 |

Page also says: annual subscription gives "over 25% monthly" savings (exact annual price not shown → **unverified**); unused signings roll over while a certificate is active; extra credentials 20 USD/month.
Signing count matters: each file signed counts as a signing `[assumption — confirm how SSL.com counts]`; a release signs the installer plus possibly bundled .exe files (yt-dlp is third-party and already signed or not, see §3).

### 1.4 Hardware token

YubiKey via SSL.com: 379.00 USD one-time per token (same OV/EV pages). Token-based signing requires a physical machine with the token attached — it cannot be used on a CI runner or remote workspace without extra tooling; here there is no GitHub Actions and the workspace is remote, so the token would live on the owner's Windows PC `[inference]`.

### 1.5 Other vendors
DigiCert, Sectigo and resellers: **not looked up** → prices `unverified` / not collected. Do not use figures from reseller comparison pages found in search results without opening the vendor's own page.

## 2. First-year cost scenarios (arithmetic on the cited list prices; not vendor quotes)

| Scenario | Computation | Total |
|---|---|---|
| A. Azure Artifact Signing Basic (if eligible) | 12 x 9.99 | 119.88 |
| B. SSL.com OV + eSigner Tier 1 (monthly billing) | 129.00 + 12 x 15.00 | 309.00 |
| C. SSL.com OV + YubiKey | 129.00 + 379.00 | 508.00 |
| D. SSL.com EV + eSigner Tier 1 | 349.00 + 12 x 15.00 | 529.00 |
| E. SSL.com EV + YubiKey | 349.00 + 379.00 | 728.00 |

Not included: expedited validation (+599), taxes, Azure subscription base costs other than the signing SKU (unverified whether any apply), time cost of identity validation.

## 3. Practical constraints for our setup

- No GitHub Actions and no Windows CI: signing has to run on the owner's Windows machine (token or eSigner/Azure client tooling) or on a Windows machine added later `[verified constraint from task brief]`.
- Tauri build integration with cloud signers (custom sign command in the Windows bundle config) exists in Tauri to my knowledge but I did not verify the exact config key or its behaviour in this workspace → `[UNVERIFIED]`; check against https://v2.tauri.app/distribute/sign/windows/ when the scaffold is built.
- What needs signing: the NSIS installer, the main app `.exe`, and our own bundled executables. Third-party binaries (yt-dlp, ffmpeg, deno) are redistributed as official release files; whether to re-sign them is a decision (the plan's risk table says "sign everything, publish checksums, use official binaries only" → note that re-signing changes the file hash relative to the vendor checksum, so runtime verification must compare against the hash of what we ship) `[OPEN]`.
- The Tauri updater key is separate and free (generated locally); see `DESIGN-NOTES.md` §4.3.
- Lead time: order during C1 per plan. Quoted validation times: Azure 1–20 business days; SSL.com 3–5 days standard.
- SmartScreen outcome after signing is **not measured**; criterion 7 is tested in C2.

## 4. Apple Developer ID (stage C4 only)

| Item | Value | Source (read 2026-10-06) |
|---|---|---|
| Apple Developer Program | 99 USD per membership year; "prices vary by region and are displayed in local currency during enrollment" | https://developer.apple.com/programs/enroll/ |
| Fee waiver | Available to eligible nonprofits, accredited educational institutions, government entities | same |
| Individual enrollment | Apple Account with 2FA, legal age, legal name, verified email/phone/address (no P.O. boxes) | same |
| Organization enrollment | Legal binding authority, **D-U-N-S number**, work email on org domain, public website on org domain | same |
| Notarization | Part of the program; any extra cost: not looked up → `unverified` | — |

macOS needs a Mac for building/notarization and testing notarization with sidecars early (plan C4). Cost of a Mac or a hosted Mac: not looked up → `unverified`.

## 5. What I could not verify

- Azure detail-pricing page numbers (placeholders in fetched copy); only the product page showed 9.99 / 99.99 / 0.005.
- SSL.com EV Sole Proprietor price (search snippet only), eSigner annual-billing prices, how signings are counted.
- Any DigiCert/Sectigo price; whether a Vietnamese entity can obtain Azure public-trust signing (list says no mention of Vietnam; confirm directly with Microsoft).
- Tax/VAT, payment methods available to a Vietnam-based buyer, Apple notarization extra costs, Mac hardware costs.
- SmartScreen behaviour for OV vs EV vs Azure-signed apps after signing.
