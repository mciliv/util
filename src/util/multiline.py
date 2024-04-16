import sys

def multiline():
    print("Enter/Paste your content below. Press Ctrl-D (Linux/Mac) or Ctrl-Z (Windows) followed by Enter to save it:")
    contents = []
    if sys.stdin.isatty():  # Check if input is interactive
        while True:
            try:
                line = input()
                contents.append(line)
            except EOFError:
                break
    else:  # Handle piped or redirected input
        for line in sys.stdin:
            contents.append(line.rstrip())  # Strip trailing newlines from stdin input
    return '\n'.join(contents)
