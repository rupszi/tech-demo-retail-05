# Golden Knowledge bucket (local stand-in)

Each file is one "trio": a question, the SQL that answers it, and how an analyst would read the
result. In production about 1,000 of these, written by the company's analysts, live as JSON files
in a cloud storage bucket behind a vector index. The real bucket does not exist yet, so the seven
files here are samples written for this prototype, in the shape a real trio would have. A local
folder of samples is the stand-in agreed with the client.

When a user asks a question, the most similar trios are added to the model's instructions, so it
applies the same definitions and the same line of reasoning the analysts used.

A trio names no brand and contains no result figures. It carries the method, so the same trio serves every
user whatever their brands; the user's own scope is applied by the SQL gate to the query the model
writes.

Tests check that the SQL in every trio still passes the SQL gate and runs, on the local database
and, in the BigQuery test group, on the real dataset, and that no trio names a brand or quotes a
result figure.
