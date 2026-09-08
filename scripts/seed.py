from api.index import app, seed_data

with app.app_context():
    seed_data()
    print("Seed complete.")
