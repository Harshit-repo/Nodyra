# Paid licenses and Stripe preparation

Nodyra launches with **manual invoicing and license delivery**. The website and
Settings → Plan & license direct buyers to sales. There is no live card checkout
or automatic license email. Community remains available without payment.

The optional Stripe backend supports **vendor-assisted checkout**: the instance
owner creates a hosted checkout link, shares it with a buyer, and delivers an
activation package after verified payment. The issuer is separate from the
static product website and from customers' self-hosted installations.

## Manual sales today

1. Agree on edition, customer name, seats, term, price, currency, taxes, and
   renewal date. Keep the agreement and invoice in private business records.
2. Confirm payment through your invoicing/payment process. Do not collect card
   details through Nodyra or email.
3. On the vendor machine, mint a key with the existing private signing key.
   Do not generate a replacement pair: shipped builds trust the existing public
   key. From `apps/api`, using your installed Python runtime:

   ```sh
   python -m tools.mint_license sign \
     --private /secure/path/license_signing_private.pem \
     --tier pro --customer "Example Company" --seats 10 --days 365
   ```

   The command prints a sensitive license key. Deliver it privately and record
   its customer, term, and invoice reference outside the repository. The annual
   term above is an example, not a pricing commitment. Omitting `--seats` uses
   edition defaults; `--seats 0` explicitly means unlimited. Omitting `--days`
   produces a perpetual key, so specify the agreed term for a time-limited sale.
4. The buyer signs in as an instance administrator, opens **Settings → Plan &
   license**, pastes the key, and selects **Apply license**. Confirm the displayed
   customer, expiry, and limits. Restart API and worker processes to apply
   features configured at startup.
5. Before expiry, invoice for the next agreed term and deliver a replacement.
   Invalid or expired replacements cannot overwrite a working key.

Alternatively, set `NODYRA_LICENSE_KEY` on the API and workers. This environment
value takes precedence; the UI cannot replace or remove it. Update the deployment
environment and restart to change an environment-managed key.

## What stays private

Private signing keys, customer license keys, refresh tokens, Stripe secret keys,
and webhook secrets are confidential. The **public verification key** embedded
in the app is meant to ship. Test signing keys use a different authority.

The local `.secrets/` directory, environment files, and internal licensing notes
are gitignored. Back up the signing key privately. Do not upload it into the
website, a release archive, customer image, issue, or pull request.

## Configure Stripe later

After business and Stripe account setup, use Stripe test mode first. Run a
separate vendor issuer with PostgreSQL, authentication, an instance owner,
HTTPS, and backups. Do not enable issuance on customer instances.

```dotenv
LICENSE_ISSUER_ENABLED=true
AUTH_REQUIRED=true
STRIPE_SECRET_KEY=sk_test_REPLACE_IN_PRIVATE_ENVIRONMENT
STRIPE_WEBHOOK_SECRET=whsec_REPLACE_IN_PRIVATE_ENVIRONMENT
STRIPE_PRICE_TIERS={"price_REPLACE_PRO":"pro","price_REPLACE_ENTERPRISE":"enterprise"}
LICENSE_VALIDITY_DAYS=45
BILLING_SUCCESS_URL=https://your-site.example/docs.html#/licensing
BILLING_CANCEL_URL=https://your-site.example/#pricing
```

Supply `LICENSE_SIGNING_KEY` privately as PEM text through the issuer's secret
configuration. Its public half must match the verifier distributed to buyers.
Checkout refuses unknown paid tiers and unusable signing keys. For an isolated
test environment, use a throwaway pair and configure its public key only there.

Create recurring Stripe prices **per installation**, with quantity fixed at one.
Pro includes its edition's ten seats; quantity is not a seat count. Configure the
Stripe portal to allow only mapped prices and quantity one. Unknown prices and
unsupported multi-item subscriptions fail closed.

Register these snapshot events:

- `checkout.session.completed`
- `customer.subscription.created`
- `customer.subscription.updated`
- `customer.subscription.deleted`

Use the externally reachable issuer URL, for example
`https://licenses.your-domain.example/api/billing/webhook` when web ingress proxies
`/api` to the API. A direct API ingress uses `/billing/webhook` instead.

Only a signed-in **instance owner** may use these vendor endpoints.
Organization-scoped API tokens and workspace-owner membership do not grant
access. Use an owner session and its CSRF token where cookie authentication is used.

| Operation | Endpoint | Request |
| --- | --- | --- |
| Create checkout link | `POST /billing/checkout` | `{"price_id":"price_REPLACE_PRO","customer_email":"buyer@example.com"}` |
| Open customer portal | `POST /billing/portal` | `{"provider_customer_id":"cus_REPLACE"}` |
| Issue/rotate activation | `POST /billing/subscriptions/{id}/activation` | No body; registry ID or Stripe `sub_...` ID |

Share `checkout_url` privately. Stripe's signed webhook synchronizes the registry
after payment. The activation endpoint returns `license_key`, `refresh_token`,
`expires_at`, and `tier`; deliver them privately to the buyer. Reissuing activation
revokes the old refresh token, while existing offline keys remain valid until
expiry. Webhook responses never contain credentials: they go to Stripe, not the
buyer. No public storefront or automated email delivery is configured.

## Payment correctness and renewal

Webhooks verify signatures on raw bytes, enforce freshness, and record event IDs.
PostgreSQL serializes subscription updates. The issuer fetches current Stripe
state inside the lock, preventing late events from reactivating cancelled
subscriptions. Activation and renewal also recheck Stripe, covering missed
webhooks. Provider outages return a retryable error and preserve installed keys.

`active`, `trialing`, and `past_due` may receive keys; `unpaid`, `incomplete`,
cancelled, paused, and unknown states may not. Normal subscriptions receive a
bounded outage margin of `LICENSE_VALIDITY_DAYS` after the reported billing-period
end. Scheduled cancellation caps new keys at the cancellation date or period end.
Expired contracts cannot receive already-expired keys.

Customers install the supplied key first, then optionally configure:

```dotenv
LICENSE_SERVER_URL=https://licenses.your-domain.example/api
LICENSE_REFRESH_TOKEN=REPLACE_WITH_PRIVATE_REFRESH_TOKEN
LICENSE_REFRESH_WINDOW_DAYS=14
```

The hourly renewal loop is off unless both settings are present. An environment-
pinned key disables automatic replacement. Verification is offline; opted-in
renewal uses HTTPS. Replacements are verified before storage, and renewal can
recover after the previous key expired. Network failures and invalid replacements
preserve the installed key. Offline keys cannot be instantly revoked after a
refund or cancellation; their expiry is the enforcement boundary.

## Before enabling real payments

Once an account exists, run a real Stripe test-mode checkout: payment, webhook,
owner activation, buyer installation, duplicate/out-of-order events, portal
cancellation, and renewal refusal. Local tests use simulated Stripe responses
and throwaway keys. They do not verify an account, live payment, payout, tax
setup, or business registration.

See Stripe's [webhook guidance](https://docs.stripe.com/webhooks) and
[subscription lifecycle](https://docs.stripe.com/billing/subscriptions/overview).
