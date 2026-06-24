def factorial(n):
    if n==0: return 1
    return n * factorial(n-1);

print("recursion of 1 is : ",factorial(1))
print("recursion of 3 is : ",factorial(3))
print("recursion of 5 is : ",factorial(5))

def open_almari(level):
    if level == 0:
        print("Andar wali almari khali hai.")
        return
    print(f"Level {level} ki almari khol raha hoon...")
    open_almari(level - 1)
    print(f"Level {level} ki almari band kar raha hoon...")

open_almari(3)

def print_1_to_n(n):
    if n == 0:
        return
    print_1_to_n(n - 1)
    print(n)

print_1_to_n(5)

def countdown(n):
    if n == 0:
        print("Blast Off!")
        return
    print(n)
    countdown(n - 1)

countdown(3)
