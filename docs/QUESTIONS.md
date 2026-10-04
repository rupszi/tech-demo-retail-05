# Questions for the client, with their answers

The brief invites questions. This page lists the twelve that were asked, the assumption the project was built on while waiting, the client's answer, and what the answer changed.

**Sent on 2026-10-04. All twelve were answered the same day.** Most answers confirmed the assumptions. The ones that did not are planned as phases 10 to 16 in [PLAN.md, section 9](PLAN.md#9-revision-after-the-clients-answers), and their progress is in the [tracker](TRACKER.md).

| # | Topic | Answer in short | Result |
|---|---|---|---|
| 1 | Which products a user may analyse | Each user sees only their brands; the CEO sees all | **Changed:** scope is by brand only (phase 11) |
| 2 | What counts as personal data | The assumed list; customer IDs are fine; demographics per individual are fine | Confirmed |
| 3 | Saved Reports library | Ours to design; any term; no recovery needed; no sharing | **Changed:** deleting is permanent (phase 12) |
| 4 | Confirmation before deleting | Yes, one confirmation is enough | Confirmed |
| 5 | Golden Knowledge bucket | Theoretical; JSON; about 1,000 trios; think about hundreds of users | Confirmed, and the design is extended (phase 15) |
| 6 | Local test data | Fine, but test on BigQuery as well | **Changed:** a BigQuery test group, and BigQuery by default (phase 13) |
| 7 | Identity and permissions | The front end sends a JWT with the user's scopes | **Changed:** profiles come from token claims (phases 11 and 15) |
| 8 | Scale and response time | Assumptions are good; long reports may take one to two minutes | Confirmed, and a time limit is added (phase 14) |
| 9 | Channels and integrations | Web chat over an API; Slack outputs maybe later | Confirmed; the design treats Slack as an output (phase 15) |
| 10 | Who changes the assistant's tone | One non-developer; an automated quality gate | **Changed** in the design (phase 15) |
| 11 | Data residency and compliance | No compliance requirements | Simplifies the design (phase 15) |
| 12 | Cost limits | Configurable; assume $1 per question; design only | **Changed** in the design (phase 15) |

---

## Questions that affect the prototype

### 1. Which products a user may analyse

The brief says: "Each user should only be able to analyze data on products related to him."

**Question.** The dataset has no field that links a user of the assistant to products. What makes a product "related" to a user?

**Assumption.** Each user is entitled to a list of brands and/or departments. For example, a brand manager sees three brands and the CEO sees everything. The restriction is applied in code to every query, so it does not depend on the model. A user sees only the products in their list, the order items for those products, the orders that contain at least one of them, and the customers who bought at least one of them.

**Follow-up asked.** An order can mix a user's products with other products. Under the assumption the order is visible, but only the user's own items and their revenue are. Is that the intended behaviour?

**Answer.** Each user can see only the brands related to them; the CEO sees all.

**What it changes.** Brand is the only scope. Scoping by department, which was an addition of ours, is removed together with the demo user that used it. "Sees all" becomes an explicit grant rather than the absence of a restriction, so a user with no brand scope sees nothing. The follow-up about mixed orders was not addressed, so that behaviour stays as assumed.

### 2. What counts as personal data

**Question.**
1. Which fields count as personal data (PII)?
2. May customers be identified by their numeric customer ID? This is needed for questions such as "who are our top customers".
3. May age, gender, city, state and country be shown for an individual customer, or only in aggregate?

**Assumption.**
1. First name, last name, email, street address, postal code, latitude and longitude are personal data. They never appear in output and cannot be used in a query at all.
2. Yes. Customers are shown as customer IDs, never by name.
3. Allowed for an individual customer ID.

**Answer.**
1. Names, email, address, postal code, coordinates.
2. Yes.
3. Showing them for an individual is fine.

**What it changes.** Nothing in the code. The documents no longer list demographics per customer as an open point, and no minimum group size is needed.

### 3. Saved Reports library

**Question.**
1. Does a store for saved reports already exist, or is it part of what should be designed?
2. For "Delete all reports mentioning Client X": is that a text search over the report's title and content? The dataset has no clients, so "X" is read as any term, such as a brand or a customer ID.
3. Should deleted reports be recoverable for some time?
4. Can reports be shared between users, or does each user only see their own?

**Assumption.**
1. It is designed and built as part of this work.
2. A case-insensitive text match on title and content.
3. Yes: deletion is a soft delete, recoverable for a period, with an audit trail of who deleted what and when.
4. Each user sees and deletes only their own reports.

**Answer.** It is part of what we design. Any term is fine. There is no need to be able to recover deleted reports. Reports are not shared between users.

**What it changes.** Deleting becomes permanent. The soft delete, the restore function and the `/undo` command are removed, and the confirmation says that the deletion cannot be undone. The audit trail stays: it records who asked, who confirmed, and the ids and titles of what was deleted.

### 4. Confirmation before deleting

The brief asks for "a strict confirmation flow before execution, without breaking UX".

**Question.** Is one explicit confirmation enough, provided it shows exactly which reports will be deleted?

**Assumption.** Yes. The assistant lists the matching reports (how many, and their titles) and asks once. Anything other than an explicit "yes" cancels. The reports deleted are exactly the ones that were listed, even if more reports are created in the meantime. The confirmation is handled by the application and cannot be given by the model on the user's behalf.

**Answer.** Yes.

**What it changes.** Nothing.

### 5. Golden Knowledge bucket

**Question.**
1. What does a stored trio look like: file format and fields? A sample trio would help.
2. Roughly how many trios are there: dozens, hundreds, thousands?
3. May answers produced by the assistant be added to the bucket, and if so, who approves them?

**Assumption.**
1. One JSON document per trio, containing the question, the SQL (BigQuery dialect), the analyst's report, and metadata such as author, date and tags.
2. Hundreds to a few thousand.
3. Answers from the assistant only become candidates. A human analyst approves a candidate before it enters the bucket.

**Answer.** The bucket is theoretical and does not need to be implemented in the prototype; a local folder with sample trios is the right stand-in, and the real bucket belongs in the HLD and the design document. The format is JSON. There are about 1,000 trios. Who approves additions is our decision in the design. We should think about how this scales with hundreds of users.

**What it changes.** Nothing in the prototype, which keeps its folder of sample trios. The design is sized for about 1,000 trios and gains a part on scale: what retrieval costs with hundreds of users, how candidates from many users are triaged before an analyst sees them, and why trios are written without brand names or figures so that one trio can serve users with different brands.

### 6. Local test data

**Question.** Is it acceptable that the automated tests run against a small local database with the same schema as the dataset, while BigQuery is used for real runs?

**Assumption.** Yes. The prototype runs against `bigquery-public-data.thelook_ecommerce` as the brief requires. The local database exists only so that tests run offline, quickly and repeatably on any machine.

**Answer.** Yes, but the client suggests testing on BigQuery as well; the free tier allows 1 TB of processing.

**What it changes.** The BigQuery checks that had been run by hand become a test group in the repository, run on request. BigQuery also becomes the default data source of the assistant, with the local database as an explicit offline mode.

---

## Questions that affect only the design document

### 7. Identity and permissions

**Question.** In production, where do user identities and their product permissions come from?

**Assumption.** Identities come from the company's single sign-on, and permissions from a central permissions table or service. The prototype selects a mock user with a command-line option.

**Answer.** Assume the front end sends a JWT with the user's scopes.

**What it changes.** The design drops the sign-in proxy and the permissions service: the API verifies the token and reads the scopes from it. In the prototype a user profile is built from token claims, and the files in `config/` are sample token payloads. One part of the earlier design no longer holds: BigQuery cannot enforce a per-user row policy from an application-level token, so brand scope is enforced by the SQL gate, and BigQuery enforces read-only access and the absence of personal data.

The claim format is our assumption: a `scopes` list with entries such as `brand:Levi's`, and `brand:*` for the CEO.

### 8. Scale and response time

**Question.** How many people will use the assistant, and what response time is acceptable?

**Assumption.** Tens to a few hundred executives, with few of them active at the same time. Ten to thirty seconds for an analysis is acceptable as long as progress is shown.

**Answer.** The assumptions look good. Long reports can even take one to two minutes.

**What it changes.** A question gets a time limit of two minutes in addition to its limits on model calls and tokens. The design no longer moves long reports to a background job.

### 9. Channels and integrations

**Question.** The prototype has a command-line interface, as required. Which channel is intended in production: web, Slack, email? And for "sending reports via mail", is there a preferred email provider?

**Assumption.** A web chat in production, built on an API so that other channels can be added. No preferred email provider: sending email is designed as a replaceable tool and is not built in the prototype.

**Answer.** In production, web chat over an API. Slack outputs might be added later.

**What it changes.** The design treats Slack as a place to send results, built as a tool like email, and not as a second chat channel.

### 10. Who changes the assistant's tone

The brief says the CEO wants to change the tone of the reports weekly, without redeployment.

**Question.** Who makes these changes, and should a change be approved before it goes live?

**Assumption.** One or two named people who are not developers edit the tone through an admin page. Each change is versioned, takes effect without a deployment, and can be rolled back. The safety rules are separate and cannot be edited there.

**Answer.** A few non-developers; one can be assumed for this task. It needs to be an automated quality gate.

**What it changes.** In the design, a tone change is published only if an automated gate passes: static checks on the text, then an evaluation run with pass thresholds. No person approves it. A change that makes live metrics worse is rolled back automatically.

### 11. Data residency and compliance

**Question.** Are there constraints on where data is stored and processed, or on sending query results to the model provider?

**Assumption.** A single region (US, where the dataset lives). In production the model is reached through Vertex AI, so data stays under the Google Cloud project's own terms.

**Answer.** No data compliance requirements.

**What it changes.** The design chooses the region for cost and latency only. The rule that personal data never appears in output comes from the brief and is unaffected.

### 12. Cost limits

**Question.** Is there a budget ceiling per question or per month?

**Assumption.** There is a cap per question, both on model usage and on the amount of data a query may scan. Above the cap the assistant declines and explains why.

**Answer.** Yes. It should be easily configurable; $1 per question can be assumed; design only.

**What it changes.** The design expresses the cap in dollars, $1 per question by default, and translates it into the limits on tokens, model calls and bytes scanned through a price table. The prototype keeps those limits as settings and does not compute dollars.
