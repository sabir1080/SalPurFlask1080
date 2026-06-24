from passlib.context import CryptContext

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")
password = "your_test_password"  # Replace with the password you used
hashed = pwd_context.hash(password)
print(f"Hashed: {hashed}")
print(f"Verify: {pwd_context.verify(password, hashed)}")