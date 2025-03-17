import os
import json

from flask import Flask, render_template, request, redirect, url_for, flash
from flask_sqlalchemy import SQLAlchemy
from flask_login import LoginManager, UserMixin, login_user, login_required, logout_user, current_user
from PyPDF2 import PdfReader
from sentence_transformers import SentenceTransformer, util
import spacy
from celery import Celery

# Flask app initialization and configuration
app = Flask(__name__)
app.config['SECRET_KEY'] = 'your_secret_key'  # Change to a secure secret
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///ai_resume_screening.db'
app.config['UPLOAD_FOLDER'] = 'uploads'
app.config['CELERY_BROKER_URL'] = 'redis://localhost:6379/0'
app.config['CELERY_RESULT_BACKEND'] = 'redis://localhost:6379/0'
# (Using only new-style settings here; avoid mixing with old names)

db = SQLAlchemy(app)
login_manager = LoginManager(app)
login_manager.login_view = 'login'

# Initialize Celery with Flask context
def make_celery(app):
    celery = Celery(app.import_name,
                    broker=app.config['CELERY_BROKER_URL'],
                    backend=app.config['CELERY_RESULT_BACKEND'])
    celery.conf.update(app.config)
    TaskBase = celery.Task

    class ContextTask(TaskBase):
        abstract = True
        def __call__(self, *args, **kwargs):
            with app.app_context():
                return TaskBase.__call__(self, *args, **kwargs)
    celery.Task = ContextTask
    return celery

celery = make_celery(app)

# Load spaCy model for entity recognition (ensure you have downloaded it with: python -m spacy download en_core_web_sm)
nlp = spacy.load("en_core_web_sm")

# Load Sentence Transformer for semantic embeddings
st_model = SentenceTransformer('all-MiniLM-L6-v2')

# --- Database Models ---
class User(db.Model, UserMixin):
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(150), unique=True, nullable=False)
    password = db.Column(db.String(150), nullable=False)  # In production, store a hash!

class Resume(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    filename = db.Column(db.String(150), nullable=False)
    text = db.Column(db.Text, nullable=False)
    extracted_entities = db.Column(db.Text)  # JSON string of entities
    embedding = db.Column(db.PickleType)       # Binary storage for embedding vector

class JobDescription(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    description = db.Column(db.Text, nullable=False)
    embedding = db.Column(db.PickleType)       # Binary storage for embedding vector

# Flask-Login user loader
@login_manager.user_loader
def load_user(user_id):
    return db.session.get(User, int(user_id))

# Utility: Check allowed file extension
def allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() == 'pdf'

# Utility: Extract text from a PDF file
def extract_text_from_pdf(filepath):
    with open(filepath, 'rb') as f:
        pdf = PdfReader(f)
        text = ""
        for page in pdf.pages:
            page_text = page.extract_text()
            if page_text:
                text += page_text
    return text

# --- Celery Task for Asynchronous Resume Processing ---
@celery.task(name="process_resume_task")
def process_resume_task(filepath, filename):
    text = extract_text_from_pdf(filepath)
    # Use spaCy to extract entities (skills, certifications, etc.)
    doc = nlp(text)
    entities = [{'text': ent.text, 'label': ent.label_} for ent in doc.ents]
    # Compute semantic embedding using Sentence Transformer
    embedding = st_model.encode(text)
    # Save the processed resume in the database
    new_resume = Resume(
        filename=filename,
        text=text,
        extracted_entities=json.dumps(entities),
        embedding=embedding
    )
    db.session.add(new_resume)
    db.session.commit()
    return True

# --- Routes ---

# Home page (with links to login/register)
@app.route("/")
def home():
    return render_template("home.html")

# User registration route
@app.route("/register", methods=["GET", "POST"])
def register():
    if request.method == "POST":
        username = request.form.get("username")
        password = request.form.get("password")
        if User.query.filter_by(username=username).first():
            flash("Username already exists")
            return redirect(url_for("register"))
        new_user = User(username=username, password=password)  # Remember: hash passwords in production!
        db.session.add(new_user)
        db.session.commit()
        flash("Registration successful. Please log in.")
        return redirect(url_for("login"))
    return render_template("register.html")

# User login route
@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        username = request.form.get("username")
        password = request.form.get("password")
        user = User.query.filter_by(username=username, password=password).first()  # Use secure hash check!
        if user:
            login_user(user)
            return redirect(url_for("dashboard"))
        else:
            flash("Invalid credentials")
    return render_template("login.html")

# User logout route
@app.route("/logout")
@login_required
def logout():
    logout_user()
    return redirect(url_for("login"))

# Dashboard for recruiters: View job descriptions and resumes
@app.route("/dashboard", methods=["GET", "POST"])
@login_required
def dashboard():
    if request.method == "POST":
        job_description_text = request.form.get("job_description")
        if job_description_text:
            job_embedding = st_model.encode(job_description_text)
            new_job = JobDescription(description=job_description_text, embedding=job_embedding)
            db.session.add(new_job)
            db.session.commit()
            flash("Job description added!")
        return redirect(url_for("dashboard"))
    
    jobs = JobDescription.query.all()
    resumes = Resume.query.all()
    return render_template("dashboard.html", jobs=jobs, resumes=resumes, username=current_user.username)

# Resume upload route
@app.route("/upload_resume", methods=["GET", "POST"])
@login_required
def upload_resume():
    if request.method == "POST":
        try:
            if 'resume' not in request.files:
                flash("No file part")
                return redirect(request.url)
            file = request.files['resume']
            if file.filename == "":
                flash("No file selected")
                return redirect(request.url)
            if file and allowed_file(file.filename):
                filepath = os.path.join(app.config['UPLOAD_FOLDER'], file.filename)
                file.save(filepath)
                # Process the resume asynchronously
                process_resume_task.delay(filepath, file.filename)
                flash("Resume uploaded and processing started.")
                return redirect(url_for("dashboard"))
            else:
                flash("Invalid file type. Only PDF files are allowed.")
                return redirect(request.url)
        except Exception as e:
            app.logger.error("Error uploading resume: %s", e)
            flash("An error occurred while uploading your resume. Please try again.")
            return redirect(request.url)
    return render_template("upload_resume.html")

# Resume Ranking route for a selected job description
@app.route("/rank/<int:job_id>")
@login_required
def rank(job_id):
    job = JobDescription.query.get_or_404(job_id)
    resumes = Resume.query.all()
    scores = []
    # Use Sentence Transformer’s cosine similarity for semantic matching
    for resume in resumes:
        sim = util.cos_sim(job.embedding, resume.embedding).item()
        scores.append((resume.filename, sim))
    # Sort by descending similarity score
    scores = sorted(scores, key=lambda x: x[1], reverse=True)
    return render_template("ranking.html", scores=scores, job=job)

if __name__ == '__main__':
    # Create necessary directories
    if not os.path.exists(app.config['UPLOAD_FOLDER']):
        os.makedirs(app.config['UPLOAD_FOLDER'])
    # Initialize the database within an application context
    with app.app_context():
        db.create_all()
    app.run(debug=True)
