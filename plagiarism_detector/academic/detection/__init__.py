"""
SmartAnalytica detection engine.

Implements the seven-step plagiarism detection workflow described in
Chapter 3, section 3.2.3 of the project documentation:

    1. Document Submission      -> handled by the submission views
    2. Preprocessing            -> preprocessing.py
    3. Initial Check            -> pipeline.py (exact-match fingerprint scan)
    4. Advanced Analysis        -> algorithms.py, structural.py, semantic.py
    5. Similarity Index         -> pipeline.py
    6. Report Generation        -> pipeline.py + highlighting.py
    7. Feedback                 -> pipeline.py (severity banding)
"""

from .pipeline import DetectionPipeline, METHOD_CHOICES, run_detection_for_assignment

__all__ = ['DetectionPipeline', 'METHOD_CHOICES', 'run_detection_for_assignment']
