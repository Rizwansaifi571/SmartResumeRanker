import streamlit as st  # for creating web apps
from PyPDF2 import PdfReader  # for reading PDF files
import pandas as pd  # for data manipulation
from sklearn.feature_extraction.text import TfidfVectorizer  # for TF-IDF transformation
from sklearn.metrics.pairwise import cosine_similarity  # for calculating similarity between documents

def extract_text_from_pdf(file):
    pdf = PdfReader(file)
    text = ""
    for page in pdf.pages:
        page_text = page.extract_text()
        if page_text:
            text += page_text
    return text

def rank_resumes(job_description, resumes):
    # Combine the job description with the resumes
    documents = [job_description] + resumes
    # Create TF-IDF vectors for all documents
    tfidf = TfidfVectorizer().fit_transform(documents)
    vectors = tfidf.toarray()
    
    # Calculate cosine similarity between job description and each resume
    job_vector = vectors[0]
    resume_vectors = vectors[1:]
    cosine_similarities = cosine_similarity([job_vector], resume_vectors).flatten()
    
    return cosine_similarities

st.title("AI Resume Screening & Candidate Ranking System")

# Job description input
st.header("Job Description")
job_description = st.text_area("Enter the Job Description")

# File upload for resumes
st.header("Upload Resumes")
uploaded_files = st.file_uploader("Upload PDF files", type=["pdf"], accept_multiple_files=True)

if uploaded_files and job_description:
    st.header("Ranking Resumes")

    resumes = []
    resume_names = []
    for uploaded_file in uploaded_files:
        text = extract_text_from_pdf(uploaded_file)
        resumes.append(text)
        resume_names.append(uploaded_file.name)

    # Rank resumes using cosine similarity
    scores = rank_resumes(job_description, resumes)

    # Create a DataFrame to display the scores
    results = pd.DataFrame({
        "Resume": resume_names,
        "Score": scores
    })
    results = results.sort_values(by="Score", ascending=False)

    st.write(results)
