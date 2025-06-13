# Use official Python slim image
FROM python:3.10

# Set working directory
WORKDIR /app

# Copy the backend code
COPY . /app

# Install Prophet/pystan system dependencies, then Python deps
RUN apt-get update && apt-get install -y \
    build-essential \
    python3-dev \
    libpython3-dev \
    libgomp1 \
    gfortran \
    libc6-dev \
    libstdc++6 \
    cmake \
    git \
    wget \
    libopenblas-dev \
    liblapack-dev \
    && rm -rf /var/lib/apt/lists/*

# Set environment variables for cmdstanpy and threading
ENV CMDSTANPY_CACHE_DIR=/tmp/cmdstanpy
ENV OMP_NUM_THREADS=1
ENV OPENBLAS_NUM_THREADS=1
ENV MKL_NUM_THREADS=1
ENV NUMEXPR_NUM_THREADS=1
ENV STAN_NUM_THREADS=1

RUN pip install --upgrade pip \
    && pip install -r requirements.txt

EXPOSE 8080

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8080"]
