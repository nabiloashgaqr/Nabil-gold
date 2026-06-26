#!/bin/bash
set -e

echo "================================================================"
echo "Nabil-gold - PUSH TO GITHUB (Finnhub ONLY)"
echo "================================================================"
echo "Current directory: $(pwd)"
echo ""

cd "$(dirname "$0")"

echo "=== Git Status ==="
git status
echo ""

echo "=== Commits ahead of origin ==="
git rev-list --count --left-right origin/main...HEAD || echo "Unable to compare (fetch may be needed)"
echo ""

echo "=== Attempting push with SSH key ==="
echo "Using key: arena-ai-nabil-gold-20260626"
echo ""

# Ensure key permissions
chmod 600 ~/.ssh/arena-ai-nabil-gold-20260626 2>/dev/null || true

# Try push
GIT_SSH_COMMAND="ssh -i ~/.ssh/arena-ai-nabil-gold-20260626 -o IdentitiesOnly=yes -o StrictHostKeyChecking=accept-new" \
git push origin main 2>&1 || {
    echo ""
    echo "❌ PUSH FAILED IN THIS ENVIRONMENT"
    echo ""
    echo "This is expected in the Arena sandbox due to libcrypto/SSH restrictions."
    echo ""
    echo "PLEASE RUN THE FOLLOWING ON YOUR LOCAL MACHINE:"
    echo ""
    echo "1. Save this EXACT SSH key to your ~/.ssh/ folder:"
    echo "   (copy everything between the lines)"
    echo ""
    echo "----- BEGIN KEY -----"
    cat ~/.ssh/arena-ai-nabil-gold-20260626 2>/dev/null || echo "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIGDyV8UU13xkbo+Btntq1cyG7YthtxaPHGIodJdXz0IS arena-ai-nabil-gold-20260626"
    echo "----- END KEY -----"
    echo ""
    echo "2. Then run these commands on your machine:"
    echo ""
    echo "   cd Nabil-gold"
    echo "   git remote -v"
    echo "   git push origin main"
    echo ""
    echo "   OR with explicit key:"
    echo "   GIT_SSH_COMMAND=\"ssh -i ~/.ssh/arena-ai-nabil-gold-20260626 -o IdentitiesOnly=yes\" git push origin main"
    echo ""
    echo "Full detailed instructions are in PUSH_INSTRUCTIONS.txt"
    echo ""
    exit 1
}

echo ""
echo "✅ SUCCESS! Changes pushed to GitHub."
git status
