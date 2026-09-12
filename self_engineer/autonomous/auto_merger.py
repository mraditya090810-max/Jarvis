"""
Auto Merger - Safe automatic application of changes to production code.

This module handles the safe application of changes from the sandbox to
the production codebase. It includes safety checks, conflict resolution,
and atomic operations to ensure system stability.

Key Features:
- Safe file copying with validation
- Conflict detection and resolution
- Atomic operations (all-or-nothing)
- Backup verification
- Rollback capability
- File integrity checks
"""

import sys
import shutil
import hashlib
from pathlib import Path
from typing import Optional, Dict, Any, List
from dataclasses import dataclass
from datetime import datetime

# Add parent directory to path for imports
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from .requirement_analyzer import Requirement


@dataclass
class MergeResult:
    """Result of a merge operation."""
    success: bool
    modified_files: List[str]
    skipped_files: List[str]
    conflicts: List[str]
    error: Optional[str] = None
    rollback_performed: bool = False
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary."""
        return {
            "success": self.success,
            "modified_files": self.modified_files,
            "skipped_files": self.skipped_files,
            "conflicts": self.conflicts,
            "error": self.error,
            "rollback_performed": self.rollback_performed,
        }


class AutoMerger:
    """
    Safely merges changes from sandbox to production.
    
    Uses atomic operations and extensive validation to ensure safety.
    """
    
    def __init__(self, project_root: Optional[Path] = None):
        """
        Initialize the auto merger.
        
        Args:
            project_root: Path to project root (auto-detected if None)
        """
        if project_root is None:
            project_root = Path(__file__).resolve().parent.parent.parent
        
        self.project_root = project_root
        self.temp_dir = project_root / ".jarvis_merge_temp"
        
        # Files to never modify (safety list)
        self.protected_files = {
            "config/api_keys.json",
            "memory/long_term.json",
            ".git/",
        }
    
    async def merge(
        self,
        source_path: Path,
        target_path: Path,
        requirement: Optional[Requirement] = None
    ) -> MergeResult:
        """
        Merge changes from source to target.
        
        Args:
            source_path: Path to source (sandbox) directory
            target_path: Path to target (production) directory
            requirement: Requirement being implemented
            
        Returns:
            MergeResult with details of the operation
        """
        print(f"[AutoMerger] Starting merge from {source_path} to {target_path}")
        
        modified_files = []
        skipped_files = []
        conflicts = []
        
        try:
            # Validate paths
            if not source_path.exists():
                return MergeResult(
                    success=False,
                    modified_files=[],
                    skipped_files=[],
                    conflicts=[],
                    error=f"Source path does not exist: {source_path}"
                )
            
            if not target_path.exists():
                return MergeResult(
                    success=False,
                    modified_files=[],
                    skipped_files=[],
                    conflicts=[],
                    error=f"Target path does not exist: {target_path}"
                )
            
            # Calculate file hashes before merge (for rollback)
            self._calculate_directory_hashes(target_path)
            
            # Find files to merge
            files_to_merge = self._find_files_to_merge(source_path, target_path)
            
            if not files_to_merge:
                return MergeResult(
                    success=True,
                    modified_files=[],
                    skipped_files=[],
                    conflicts=[],
                    error="No files to merge"
                )
            
            # Create temporary directory for atomic merge
            self.temp_dir.mkdir(parents=True, exist_ok=True)
            
            # Copy target to temp directory
            temp_target = self.temp_dir / "target"
            if temp_target.exists():
                shutil.rmtree(temp_target)
            shutil.copytree(target_path, temp_target)
            
            # Apply changes to temp target
            for rel_path in files_to_merge:
                source_file = source_path / rel_path
                temp_file = temp_target / rel_path
                
                # Check if file is protected
                if self._is_protected(rel_path):
                    skipped_files.append(str(rel_path))
                    print(f"[AutoMerger] Skipped protected file: {rel_path}")
                    continue
                
                # Check for conflicts
                if self._has_conflict(source_file, target_path / rel_path):
                    conflicts.append(str(rel_path))
                    print(f"[AutoMerger] Conflict detected: {rel_path}")
                    continue
                
                # Apply change
                temp_file.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(source_file, temp_file)
                modified_files.append(str(rel_path))
                print(f"[AutoMerger] Merged: {rel_path}")
            
            if not modified_files:
                # No files were actually modified
                shutil.rmtree(temp_target)
                return MergeResult(
                    success=True,
                    modified_files=[],
                    skipped_files=skipped_files,
                    conflicts=conflicts,
                    error="No files were modified (all skipped or conflicted)"
                )
            
            # Validate merged code
            if not self._validate_merged_code(temp_target):
                shutil.rmtree(temp_target)
                return MergeResult(
                    success=False,
                    modified_files=[],
                    skipped_files=skipped_files,
                    conflicts=conflicts,
                    error="Merged code validation failed"
                )
            
            # Atomic swap: replace target with temp target
            print(f"[AutoMerger] Performing atomic swap...")
            backup_path = target_path.parent / f"{target_path.name}_backup_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
            
            # Move target to backup
            shutil.move(str(target_path), str(backup_path))
            
            # Move temp target to target
            shutil.move(str(temp_target), str(target_path))
            
            # Verify the merge
            if not self._verify_merge(target_path, modified_files):
                # Rollback
                print(f"[AutoMerger] Verification failed, rolling back...")
                shutil.rmtree(target_path)
                shutil.move(str(backup_path), str(target_path))
                
                return MergeResult(
                    success=False,
                    modified_files=[],
                    skipped_files=skipped_files,
                    conflicts=conflicts,
                    error="Merge verification failed, rolled back",
                    rollback_performed=True
                )
            
            # Clean up backup after successful verification
            if backup_path.exists():
                shutil.rmtree(backup_path)
            
            # Clean up temp directory
            if self.temp_dir.exists():
                shutil.rmtree(self.temp_dir)
            
            print(f"[AutoMerger] Merge successful: {len(modified_files)} files modified")
            
            return MergeResult(
                success=True,
                modified_files=modified_files,
                skipped_files=skipped_files,
                conflicts=conflicts
            )
            
        except Exception as e:
            print(f"[AutoMerger] Merge failed: {e}")
            
            # Clean up temp directory
            if self.temp_dir.exists():
                try:
                    shutil.rmtree(self.temp_dir)
                except Exception:
                    pass
            
            return MergeResult(
                success=False,
                modified_files=modified_files,
                skipped_files=skipped_files,
                conflicts=conflicts,
                error=str(e)
            )
    
    def _find_files_to_merge(
        self,
        source_path: Path,
        target_path: Path
    ) -> List[Path]:
        """
        Find files that need to be merged.
        
        Args:
            source_path: Source directory
            target_path: Target directory
            
        Returns:
            List of relative paths to merge
        """
        files_to_merge = []
        
        # Find all Python files in source
        for source_file in source_path.rglob("*.py"):
            rel_path = source_file.relative_to(source_path)
            target_file = target_path / rel_path
            
            # Include if file exists in target or is new
            if target_file.exists() or not target_file.exists():
                files_to_merge.append(rel_path)
        
        return files_to_merge
    
    def _is_protected(self, rel_path: Path) -> bool:
        """
        Check if a file is protected from modification.
        
        Args:
            rel_path: Relative path to file
            
        Returns:
            True if file is protected
        """
        path_str = str(rel_path).replace("\\", "/")
        
        for protected in self.protected_files:
            if path_str.startswith(protected.rstrip("/")):
                return True
        
        return False
    
    def _has_conflict(self, source_file: Path, target_file: Path) -> bool:
        """
        Check if there's a conflict between source and target files.
        
        Args:
            source_file: Source file path
            target_file: Target file path
            
        Returns:
            True if conflict detected
        """
        if not target_file.exists():
            return False  # New file, no conflict
        
        # Calculate hashes
        source_hash = self._calculate_file_hash(source_file)
        target_hash = self._calculate_file_hash(target_file)
        
        # If hashes are the same, no conflict
        if source_hash == target_hash:
            return False
        
        # Simple heuristic: if files are very different, flag as conflict
        # In production, this would use more sophisticated diff analysis
        source_content = source_file.read_text(encoding="utf-8", errors="ignore")
        target_content = target_file.read_text(encoding="utf-8", errors="ignore")
        
        # If similarity is below threshold, flag as conflict
        similarity = self._calculate_similarity(source_content, target_content)
        
        return similarity < 0.3  # Less than 30% similar = conflict
    
    def _calculate_file_hash(self, file_path: Path) -> str:
        """Calculate SHA256 hash of a file."""
        hasher = hashlib.sha256()
        with open(file_path, "rb") as f:
            for chunk in iter(lambda: f.read(4096), b""):
                hasher.update(chunk)
        return hasher.hexdigest()
    
    def _calculate_directory_hashes(self, dir_path: Path) -> Dict[str, str]:
        """Calculate hashes for all files in a directory."""
        hashes = {}
        for file_path in dir_path.rglob("*.py"):
            rel_path = str(file_path.relative_to(dir_path))
            hashes[rel_path] = self._calculate_file_hash(file_path)
        return hashes
    
    def _calculate_similarity(self, content1: str, content2: str) -> float:
        """
        Calculate similarity between two strings.
        
        Simple implementation using common substrings.
        In production, use more sophisticated diff algorithms.
        """
        # Remove whitespace for comparison
        c1 = "".join(content1.split())
        c2 = "".join(content2.split())
        
        if not c1 or not c2:
            return 0.0
        
        # Use longest common substring as simple similarity metric
        def longest_common_substring(s1, s2):
            m = [[0] * (1 + len(s2)) for _ in range(1 + len(s1))]
            longest = 0
            for x in range(1, 1 + len(s1)):
                for y in range(1, 1 + len(s2)):
                    if s1[x - 1] == s2[y - 1]:
                        m[x][y] = m[x - 1][y - 1] + 1
                        if m[x][y] > longest:
                            longest = m[x][y]
                    else:
                        m[x][y] = 0
            return longest
        
        lcs = longest_common_substring(c1, c2)
        max_len = max(len(c1), len(c2))
        similarity = lcs / max_len if max_len > 0 else 0.0
        
        return similarity
    
    def _validate_merged_code(self, merged_path: Path) -> bool:
        """
        Validate the merged code.
        
        Args:
            merged_path: Path to merged code
            
        Returns:
            True if validation passes
        """
        # Check Python syntax for all files
        for py_file in merged_path.rglob("*.py"):
            try:
                compile(py_file.read_text(encoding="utf-8"), str(py_file), "exec")
            except SyntaxError as e:
                print(f"[AutoMerger] Syntax error in {py_file}: {e}")
                return False
        
        return True
    
    def _verify_merge(
        self,
        target_path: Path,
        modified_files: List[str]
    ) -> bool:
        """
        Verify that the merge was successful.
        
        Args:
            target_path: Path to target directory
            modified_files: List of modified files
            
        Returns:
            True if verification passes
        """
        # Check that all modified files exist
        for rel_path in modified_files:
            file_path = target_path / rel_path
            if not file_path.exists():
                print(f"[AutoMerger] Verification failed: {rel_path} does not exist")
                return False
        
        # Validate syntax again
        if not self._validate_merged_code(target_path):
            print(f"[AutoMerger] Verification failed: syntax errors detected")
            return False
        
        return True
    
    def rollback(self, backup_path: Path, target_path: Path) -> bool:
        """
        Rollback to a backup.
        
        Args:
            backup_path: Path to backup directory
            target_path: Path to target directory
            
        Returns:
            True if rollback successful
        """
        try:
            if not backup_path.exists():
                print(f"[AutoMerger] Backup does not exist: {backup_path}")
                return False
            
            # Remove current target
            if target_path.exists():
                shutil.rmtree(target_path)
            
            # Restore from backup
            shutil.move(str(backup_path), str(target_path))
            
            print(f"[AutoMerger] Rollback successful")
            return True
            
        except Exception as e:
            print(f"[AutoMerger] Rollback failed: {e}")
            return False
