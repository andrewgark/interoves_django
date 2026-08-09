# Payment/legal operations checklist

This file is internal. None of these labels or operational notes may be rendered in public checkout or legal HTML.

## ARMENIAN_ACQUIRING_BLOCKER

International card checkout stays disabled until all items below are obtained from the selected Armenian bank/payment provider and implemented against its official documentation:

- official API/VPOS contract;
- merchant credentials;
- test and production endpoints;
- signing/signature algorithm and key lifecycle;
- callback requirements and authenticated status transitions;
- success, failure, cancel, and return URLs;
- idempotency and payment-status API;
- refund and cancel API;
- exact provider display name;
- bank-specific mandatory customer copy.

Do not publish a provider name, credentials, endpoints, or callback behavior before these are confirmed. `vpos` in `games.payment_routes` is an internal placeholder, not a public bank or merchant name.

## CRYPTO_LEGAL_REVIEW

The production data model and creation flow currently bind new NOWPayments ticket orders to `ru_self_employed`, with the price fixed in RUB. Preserve that mapping during technical maintenance unless the owner supplies a reviewed replacement merchant model.

The crypto route must remain separate from YooKassa and Armenian card acquiring. A specialist review of the Russian-law treatment of this route remains advisable before changing its seller, pricing semantics, supported assets, or public availability. This review must never be exposed as a public checkout or Terms marker.

## PRIVACY_INFRA_COMPLIANCE_BLOCKER

Verified on 9 August 2026:

- Django primary MySQL database: Amazon RDS, `eu-central-1a` (Frankfurt), not publicly accessible;
- application/ALB: Elastic Beanstalk `eu-central-1`;
- Redis channel/cache infrastructure: ElastiCache `eu-central-1`, transit and at-rest encryption enabled;
- media/static bucket: Amazon S3 `eu-central-1`;
- CloudWatch application log retention configured to 1 day.

The primary collection and storage database is outside Russia. The repository does not contain evidence of a Russian primary database, a Roskomnadzor processing notification, or a cross-border-transfer notification. Do not try to cure this with policy copy. The owner must obtain Russian privacy counsel, confirm whether an exception applies, and either document the compliant architecture/filings or plan a safely tested localization migration.

## Data-subject and retention workflow

Owner: site administrator. Review cadence: at least quarterly and whenever a verified data request arrives.

1. Receive requests at `andrewgarkavyy@gmail.com` and verify control of the relevant account without collecting unnecessary identity documents.
2. Record the request date privately and complete access/correction/deletion work within 30 days unless a mandatory exception or active dispute applies.
3. Before deletion, identify payment/tax records and active disputes that must be retained. Keep the minimum link needed for those records; do not delete paid-order accounting records before the applicable period (operational minimum: 5 years from the reporting period/operation).
4. Remove or anonymize unneeded `auth.User`, `Profile`, social-account, team-membership, and personal game identifiers. Preserve tournament/team history only in anonymized or minimized form. Review cascading relationships in a database backup/staging copy before applying a production deletion.
5. Review resolved `BugReport` rows and completed `CorporateGameOrder` records older than 3 years; delete or anonymize their reporter/contact fields unless tied to an active payment or dispute. Apply the same 3-year rule to corresponding support email and private Telegram copies.
6. Keep ordinary access/error/security logs no longer than 12 months. Current CloudWatch retention is 1 day; extend a specific incident record only while it is needed for an active investigation, dispute, abuse case, or legal request.
7. Record completion without retaining the deleted personal data itself. Recheck that provider-side copies (email, Telegram, authentication provider, or payment provider) are handled under the applicable provider process where a request reaches those systems.

Before any bulk production cleanup, take a recoverable backup, run a dry-run inventory, and review protected payment/accounting rows separately.
