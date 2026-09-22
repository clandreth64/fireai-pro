"""Human review: persistent corrections and the verification gate (Milestone 1.6).

FireAI's machine interpretation and human truth are stored separately:
the model carries what FireAI derived; the review store carries what a
person confirmed or corrected. The pipeline never writes to the review store.
"""
