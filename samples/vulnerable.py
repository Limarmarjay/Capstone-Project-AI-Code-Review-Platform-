import subprocess

password = "super_secret_password"

def run_command(user_input):
    subprocess.run(
        user_input,
        shell=True
    )