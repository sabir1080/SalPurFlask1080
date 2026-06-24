
a=input("All data will be deleted.... want to delete y/n.....: ")
if a.upper()=="Y":
    from app import app, db, Supplier, Customer, Item, Purchase, Sale
    from datetime import datetime

    # delete database
    with app.app_context():
        db.drop_all()
        db.session.commit()
        print("All data has been deleted..")
else:
    print("No change have been done....")
