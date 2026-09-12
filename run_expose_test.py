import sys, time
sys.path.insert(0, r'D:\CLI-paper\opendraft\engine')
from draft_generator import generate_draft

t0 = time.time()
pdf, docx = generate_draft(
    topic="Graph Neural Networks for Molecule Property Prediction",
    language="en",
    academic_level="research_paper",
    skip_validation=True,
    output_type="expose",
)
print(f"\nOK pdf={pdf}\nOK docx={docx}\nelapsed={time.time()-t0:.0f}s")
