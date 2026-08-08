"""Word document manager - check what's open, provide options."""

from .applescript import run_applescript_safe
from .config import LOGGER


def is_word_running() -> bool:
    """Check if Word is running."""
    script = 'tell application "System Events" to (name of processes) contains "Microsoft Word"'
    stdout, stderr, code = run_applescript_safe(script, timeout=5)
    return "true" in stdout.lower()


def get_open_documents() -> list:
    """Get list of open Word documents with paths."""
    script = '''
tell application "Microsoft Word"
    set res to ""
    set docList to documents
    repeat with d in docList
        set dName to name of d
        set dSaved to saved of d
        set dPath to ""
        try
            set dPath to full name of d
        end try
        set res to res & dName & "|" & dPath & "|" & (dSaved as text) & linefeed
    end repeat
    return res
end tell
'''
    stdout, stderr, code = run_applescript_safe(script, timeout=10)
    
    if code != 0:
        return []
    
    # Parse output
    docs = []
    for line in stdout.strip().split('\n'):
        if line.strip():
            parts = line.split('|')
            if len(parts) >= 2:
                docs.append({
                    'name': parts[0].strip(),
                    'path': parts[1].strip() if len(parts) > 1 and parts[1].strip() else 'Untitled',
                    'saved': parts[2].strip().lower() == 'true' if len(parts) > 2 else False
                })
    
    return docs


def create_new_document(filename: str = None) -> bool:
    """Create a new Word document."""
    script = '''
tell application "Microsoft Word"
    activate
    create new document
end tell
'''
    stdout, stderr, code = run_applescript_safe(script, timeout=10)
    return code == 0


def open_document(file_path: str) -> bool:
    """Open an existing Word document."""
    script = f'''
tell application "Microsoft Word"
    activate
    open file "{file_path}"
end tell
'''
    stdout, stderr, code = run_applescript_safe(script, timeout=15)
    return code == 0


def open_file_dialog() -> str:
    """Show native macOS file open dialog."""
    script = '''
tell application "System Events"
    activate
    try
        set chosenFile to choose file with prompt "Select Word document to open"
        return POSIX path of chosenFile
    on error
        return ""
    end try
end tell
'''
    stdout, stderr, code = run_applescript_safe(script, timeout=30)
    
    if code == 0 and stdout.strip():
        return stdout.strip()
    return None


def manage_word_documents() -> bool:
    """
    Check Word status and manage documents.
    Returns True if successfully selected/opened a document, False otherwise.
    """
    
    LOGGER.info("Checking Word status...")
    
    # Check if Word is running
    if not is_word_running():
        LOGGER.info("Word is not running.")
        print("\n📄 Word Management")
        print("─" * 40)
        print("1. Create new document")
        print("2. Open existing document")
        print("3. Cancel")
        
        choice = input("\nSelect option (1-3): ").strip()
        
        if choice == "1":
            LOGGER.info("Creating new document...")
            if create_new_document():
                LOGGER.info("✓ New document created")
                return True
            else:
                LOGGER.error("Failed to create new document")
                return False
        elif choice == "2":
            LOGGER.info("Opening file dialog...")
            file_path = open_file_dialog()
            if file_path:
                LOGGER.info(f"Opening {file_path}...")
                if open_document(file_path):
                    LOGGER.info(f"✓ Opened {file_path}")
                    return True
            else:
                LOGGER.info("No file selected")
                return False
        else:
            LOGGER.info("Cancelled")
            return False
    
    # Word is running - check for open documents
    docs = get_open_documents()
    
    if not docs:
        LOGGER.info("No documents open in Word.")
        print("\n📄 Word Management")
        print("─" * 40)
        print("1. Create new document")
        print("2. Open existing document")
        print("3. Cancel")
        
        choice = input("\nSelect option (1-3): ").strip()
        
        if choice == "1":
            LOGGER.info("Creating new document...")
            if create_new_document():
                LOGGER.info("✓ New document created")
                return True
            else:
                LOGGER.error("Failed to create new document")
                return False
        elif choice == "2":
            LOGGER.info("Opening file dialog...")
            file_path = open_file_dialog()
            if file_path:
                LOGGER.info(f"Opening {file_path}...")
                if open_document(file_path):
                    LOGGER.info(f"✓ Opened {file_path}")
                    return True
            else:
                LOGGER.info("No file selected")
                return False
        else:
            LOGGER.info("Cancelled")
            return False
    
    # Documents are open
    LOGGER.info(f"Found {len(docs)} open document(s)")
    print("\n📄 Word Management")
    print("─" * 40)
    print("Open documents:")
    for i, doc in enumerate(docs, 1):
        status = "✓ saved" if doc['saved'] else "• unsaved"
        print(f"  {i}. {doc['name']} ({status})")
    print("\nOptions:")
    print(f"  {len(docs) + 1}. Create new document")
    print(f"  {len(docs) + 2}. Open other document")
    print(f"  {len(docs) + 3}. Cancel")
    
    choice = input("\nSelect option: ").strip()
    
    try:
        choice_num = int(choice)
        
        if 1 <= choice_num <= len(docs):
            # Use selected document (it's already open, just continue)
            selected = docs[choice_num - 1]
            LOGGER.info(f"Using document: {selected['name']}")
            return True
        
        elif choice_num == len(docs) + 1:
            LOGGER.info("Creating new document...")
            if create_new_document():
                LOGGER.info("✓ New document created")
                return True
            else:
                LOGGER.error("Failed to create new document")
                return False
        
        elif choice_num == len(docs) + 2:
            LOGGER.info("Opening file dialog...")
            file_path = open_file_dialog()
            if file_path:
                LOGGER.info(f"Opening {file_path}...")
                if open_document(file_path):
                    LOGGER.info(f"✓ Opened {file_path}")
                    return True
            else:
                LOGGER.info("No file selected")
                return False
        
        elif choice_num == len(docs) + 3:
            LOGGER.info("Cancelled")
            return False
        
        else:
            LOGGER.warning("Invalid option")
            return False
    
    except ValueError:
        LOGGER.warning("Invalid input")
        return False
