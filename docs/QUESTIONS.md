# Questions for the client, and the assumptions used meanwhile

The brief invites questions. This is the full list, and for each one the assumption the project is built on until an answer arrives. **Nothing here blocks the work**: if a question is not answered, the assumption stands and is stated in the documentation. When an answer arrives it is recorded here and in the [tracker](TRACKER.md), and the affected code or design is updated.

Questions 1 to 6 affect the prototype. Questions 7 to 12 affect only the design document.

| # | Topic | Affects | Status |
|---|---|---|---|
| 1 | Which products a user may analyse | Prototype | Awaiting answer |
| 2 | What counts as personal data | Prototype | Awaiting answer |
| 3 | Saved Reports library | Prototype | Awaiting answer |
| 4 | Confirmation before deleting | Prototype | Awaiting answer |
| 5 | Golden Knowledge bucket | Prototype (small), design | Awaiting answer |
| 6 | Local test data | Prototype | Awaiting answer |
| 7 | Identity and permissions | Design | Awaiting answer |
| 8 | Scale and response time | Design | Awaiting answer |
| 9 | Channels and integrations | Design | Awaiting answer |
| 10 | Who changes the assistant's tone | Design | Awaiting answer |
| 11 | Data residency and compliance | Design | Awaiting answer |
| 12 | Cost limits | Design | Awaiting answer |

---

## Questions that affect the prototype

### 1. Which products a user may analyse

The brief says: "Each user should only be able to analyze data on products related to him."

**Question.** The dataset has no field that links a user of the assistant to products. What makes a product "related" to a user?

**Assumption.** Each user is entitled to a list of brands and/or departments. For example, a brand manager sees three brands and the CEO sees everything. The restriction is applied in code to every query, so it does not depend on the model. A user sees only the products in their list, the order items for those products, the orders that contain at least one of them, and the customers who bought at least one of them.

**Follow-up.** An order can mix a user's products with other products. Under the assumption the order is visible, but only the user's own items and their revenue are. Is that the intended behaviour?

**Answer.** Awaiting.

### 2. What counts as personal data

**Question.**
1. Which fields count as personal data (PII)?
2. May customers be identified by their numeric customer ID? This is needed for questions such as "who are our top customers".
3. May age, gender, city, state and country be shown for an individual customer, or only in aggregate?

**Assumption.**
1. First name, last name, email, street address, postal code, latitude and longitude are personal data. They never appear in output and cannot be used in a query at all.
2. Yes. Customers are shown as customer IDs, never by name.
3. Allowed for an individual customer ID. Restricting demographics to aggregates with a minimum group size is described in the design as a production option.

**Answer.** Awaiting.

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
4. Each user sees and deletes only their own reports. Sharing is out of scope for the prototype.

**Answer.** Awaiting.

### 4. Confirmation before deleting

The brief asks for "a strict confirmation flow before execution, without breaking UX".

**Question.** Is one explicit confirmation enough, provided it shows exactly which reports will be deleted?

**Assumption.** Yes. The assistant lists the matching reports (how many, and their titles) and asks once. Anything other than an explicit "yes" cancels. The reports deleted are exactly the ones that were listed, even if more reports are created in the meantime. The confirmation is handled by the application and cannot be given by the model on the user's behalf.

**Answer.** Awaiting.

### 5. Golden Knowledge bucket

**Question.**
1. What does a stored trio look like: file format and fields? A sample trio would help.
2. Roughly how many trios are there: dozens, hundreds, thousands?
3. May answers produced by the assistant be added to the bucket, and if so, who approves them?

**Assumption.**
1. One JSON document per trio, containing the question, the SQL (BigQuery dialect), the analyst's report, and metadata such as author, date and tags.
2. Hundreds to a few thousand, which is what the retrieval design is sized for.
3. Answers from the assistant only become candidates. A human analyst approves a candidate before it enters the bucket.

Because the brief describes the bucket as theoretical, the prototype uses a small set of sample trios in a local folder.

**Answer.** Awaiting.

### 6. Local test data

**Question.** Is it acceptable that the automated tests run against a small local database with the same schema as the dataset, while BigQuery is used for real runs?

**Assumption.** Yes. The prototype runs against `bigquery-public-data.thelook_ecommerce` as the brief requires. The local database exists only so that tests run offline, quickly and repeatably on any machine.

**Answer.** Awaiting.

---

## Questions that affect only the design document

### 7. Identity and permissions

**Question.** In production, where do user identities and their product permissions come from?

**Assumption.** Identities come from the company's single sign-on, and permissions from a central permissions table or service. The prototype selects a mock user with a command-line option.

**Answer.** Awaiting.

### 8. Scale and response time

**Question.** How many people will use the assistant, and what response time is acceptable?

**Assumption.** Tens to a few hundred executives, with few of them active at the same time. Ten to thirty seconds for an analysis is acceptable as long as progress is shown.

**Answer.** Awaiting.

### 9. Channels and integrations

**Question.** The prototype has a command-line interface, as required. Which channel is intended in production: web, Slack, email? And for "sending reports via mail", is there a preferred email provider?

**Assumption.** A web chat in production, built on an API so that other channels can be added. No preferred email provider: sending email is designed as a replaceable tool and is not built in the prototype.

**Answer.** Awaiting.

### 10. Who changes the assistant's tone

The brief says the CEO wants to change the tone of the reports weekly, without redeployment.

**Question.** Who makes these changes, and should a change be approved before it goes live?

**Assumption.** One or two named people who are not developers edit the tone through an admin page. Each change is versioned, takes effect without a deployment, and can be rolled back. The safety rules are separate and cannot be edited there.

**Answer.** Awaiting.

### 11. Data residency and compliance

**Question.** Are there constraints on where data is stored and processed, or on sending query results to the model provider?

**Assumption.** A single region (US, where the dataset lives). In production the model is reached through Vertex AI, so data stays under the Google Cloud project's own terms.

**Answer.** Awaiting.

### 12. Cost limits

**Question.** Is there a budget ceiling per question or per month?

**Assumption.** There is a cap per question, both on model usage and on the amount of data a query may scan. Above the cap the assistant declines and explains why.

**Answer.** Awaiting.
