# Use a lightweight Python base image
FROM python:3.10-slim

# Prevent Python from writing pyc files and buffering stdout/stderr
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

# Install system dependencies required for serial and build tools
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    python3-dev \
    && rm -rf /var/lib/apt-get/lists/*

WORKDIR /app

# Copy requirements and install dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy the application code
COPY . .

# Change working directory to where app.py and templates reside
WORKDIR /app/app

# Expose Flask port
EXPOSE 5050

# Run the application
CMD ["python", "app.py"]