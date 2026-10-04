# Golden Knowledge bucket (local stand-in)

Each file is one "trio" written by a human analyst: a question, the SQL used to answer it, and how
the analyst interpreted the result. In production these live in a cloud storage bucket behind a
vector index; here they are a folder, which is the structure agreed with the client for the
prototype.

When a user asks a question, the most similar trios are added to the model's instructions, so it
applies the same definitions and the same line of reasoning the analysts used.

A test checks that the SQL in every trio still passes the SQL gate and runs.
