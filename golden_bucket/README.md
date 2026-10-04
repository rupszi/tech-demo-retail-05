# Golden Knowledge bucket (local stand-in)

Each file is one "trio" written by a human analyst: a question, the SQL used to answer it, and how
the analyst interpreted the result. In production about 1,000 of these live as JSON files in a
cloud storage bucket behind a vector index; here they are a folder with seven samples, which is the
structure agreed with the client for the prototype.

When a user asks a question, the most similar trios are added to the model's instructions, so it
applies the same definitions and the same line of reasoning the analysts used.

A trio names no brand and contains no result figures. It carries the method, so the same trio serves every
user whatever their brands; the user's own scope is applied by the SQL gate to the query the model
writes.

Tests check that the SQL in every trio still passes the SQL gate and runs, on the local database
and, in the BigQuery test group, on the real dataset.
