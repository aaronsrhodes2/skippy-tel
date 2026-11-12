FROM python:3.12-slim
RUN useradd --create-home popup
USER popup
WORKDIR /app
COPY echo.py ./
EXPOSE 47410
CMD ["python", "-u", "echo.py"]
