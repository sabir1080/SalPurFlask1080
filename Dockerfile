#Dockerfile for the application

FROM python:3.14.0

WORKDIR /app

COPY . .

RUN ["python", "-u", "app.py"]

#RUN pip install -r requirements.txt

