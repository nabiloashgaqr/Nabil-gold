#!/bin/bash
# Push script for Nabil-gold

echo "=== Pushing Nabil-gold to GitHub ==="
echo ""

# Ensure we're in the right directory
cd "$(dirname "$0")"

# Show current status
echo "Current status:"
git status --short
echo ""
echo "Commits to push:"
git log --oneline origin/main..HEAD
echo ""

# Try to push
echo "Attempting push..."
git push origin main

if [ $? -eq 0 ]; then
    echo ""
    echo "✅ SUCCESS! All changes pushed to GitHub."
else
    echo ""
    echo "❌ Push failed."
    echo ""
    echo "Possible solutions:"
    echo "1. Make sure you have the SSH key added to your GitHub account:"
    echo "   ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIGDyV8UU13xkbo+Btntq1cyG7YthtxaPHGIodJdXz0IS arena-ai-nabil-gold-20260626"
    echo ""
    echo "2. Add the key to ssh-agent:"
    echo "   eval \"\$(ssh-agent -s)\""
    echo "   ssh-add ~/.ssh/arena-ai-nabil-gold-20260626"
    echo ""
    echo "3. Test the connection:"
    echo "   ssh -T git@github.com"
fi
