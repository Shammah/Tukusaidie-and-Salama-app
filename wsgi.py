from app import create_app

app = create_app()

if __name__ == "__main__":
    # local development server; Render runs gunicorn wsgi:app
    app.run(host="127.0.0.1", port=5000, debug=app.config["APP_ENV"] != "production")
