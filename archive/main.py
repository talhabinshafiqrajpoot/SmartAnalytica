from typing import Set, Tuple, List
from collections import Counter
import math

def text_to_set(file_path: str) -> Set[str]:
    """
    Reads a file and returns a set of words in the file.
    """
    with open(file_path, 'r', encoding='utf-8') as file:
        text = file.read()
    return set(text.lower().split())

def text_to_list(file_path: str) -> List[str]:
    """
    Reads a file and returns a list of words in the file.
    """
    with open(file_path, 'r', encoding='utf-8') as file:
        text = file.read()
    return text.lower().split()

def text_to_vector(text: str) -> Counter:
    """
    Convert text to a vector (frequency counts of words).
    """
    words = text.split()
    return Counter(words)

def jaccard_similarity(set1: Set[str], set2: Set[str]) -> Tuple[float, int, int]:
    """
    Computes the Jaccard Similarity between two sets and returns additional information.
    """
    intersection = set1.intersection(set2)
    union = set1.union(set2)
    similarity = len(intersection) / len(union) if union else 0
    return similarity * 100, len(intersection), len(union) - len(intersection)

def lcs(X: List[str], Y: List[str]) -> Tuple[int, float, int]:
    """
    Computes the Longest Common Subsequence (LCS) and returns additional information.
    """
    m, n = len(X), len(Y)
    L = [[0] * (n + 1) for _ in range(m + 1)]
    for i in range(1, m + 1):
        for j in range(1, n + 1):
            if X[i - 1] == Y[j - 1]:
                L[i][j] = L[i - 1][j - 1] + 1
            else:
                L[i][j] = max(L[i - 1][j], L[i][j - 1])
    lcs_length = L[m][n]
    similarity_percentage = (lcs_length / min(m, n) * 100) if min(m, n) else 0
    return lcs_length, similarity_percentage, max(m, n) - lcs_length

def levenshtein_distance(s1: str, s2: str) -> Tuple[int, float, int, int]:
    """
    Computes the Levenshtein distance and returns additional information.
    """
    if len(s1) < len(s2):
        return levenshtein_distance(s2, s1)
    previous_row = range(len(s2) + 1)
    for i, c1 in enumerate(s1):
        current_row = [i + 1]
        for j, c2 in enumerate(s2):
            insertions = previous_row[j + 1] + 1
            deletions = current_row[j] + 1
            substitutions = previous_row[j] + (c1 != c2)
            current_row.append(min(insertions, deletions, substitutions))
        previous_row = current_row
    distance = previous_row[-1]
    total_chars = max(len(s1), len(s2))
    similarity_percentage = (1 - distance / total_chars) * 100
    return distance, similarity_percentage, total_chars - distance, distance

def cosine_similarity(vec1: Counter, vec2: Counter) -> Tuple[float, int, int]:
    """
    Calculate the cosine similarity between two text vectors.
    """
    intersection = set(vec1.keys()) & set(vec2.keys())
    numerator = sum([vec1[x] * vec2[x] for x in intersection])
    sum1 = sum([vec1[x]**2 for x in vec1.keys()])
    sum2 = sum([vec2[x]**2 for x in vec2.keys()])
    denominator = math.sqrt(sum1) * math.sqrt(sum2)
    if not denominator:
        return 0.0, 0, len(vec1) + len(vec2)
    cosine = numerator / denominator
    return cosine * 100, len(intersection), (len(vec1) + len(vec2) - 2 * len(intersection))

def rabin_karp(text: str, patterns: List[str]) -> Tuple[int, int, float]:
    """
    Rabin-Karp algorithm to find the number of matching and non-matching words.
    """
    d = 256  # Number of characters in the input alphabet
    q = 101  # A prime number
    results = []
    text_length = len(text)

    for pattern in patterns:
        m = len(pattern)
        if m == 0 or text_length < m:
            continue
        h = pow(d, m-1) % q
        p = 0  # Hash value for pattern
        t = 0  # Hash value for text window
        for i in range(m):  # Calculate the hash value of the pattern and the first window of the text
            p = (d * p + ord(pattern[i])) % q
            t = (d * t + ord(text[i])) % q

        # Slide the pattern over text one by one
        for i in range(text_length - m + 1):
            # Check the hash values of current window of text and pattern
            if p == t:
                # Check for characters one by one
                if text[i:i+m] == pattern:
                    results.append(pattern)
                    break  # Break after the first match to avoid counting multiple times

            # Calculate the hash value for the next window of text: Remove leading digit, add trailing digit
            if i < text_length - m:
                t = (d*(t - ord(text[i])*h) + ord(text[i+m])) % q
                # We might get negative values of t, converting it to positive
                if t < 0:
                    t = t + q

    common_words = len(results)
    all_words = len(text.split())
    unique_words = all_words - common_words
    similarity_percentage = common_words / all_words * 100 if all_words else 0

    return common_words, unique_words, similarity_percentage

# Example usage
file1 = 'TestFiles/file_1.txt'
file2 = 'TestFiles/file_2.txt'

# Convert text to sets and lists of words
words1_set = text_to_set(file1)
words2_set = text_to_set(file2)
words1_list = text_to_list(file1)
words2_list = text_to_list(file2)
words1_text = ' '.join(words1_list)
words2_text = ' '.join(words2_list)
vec1 = text_to_vector(words1_text)
vec2 = text_to_vector(words2_text)

# Assume patterns is the list of unique words from the second file
patterns = list(set(words2_list))

# Calculate Jaccard Similarity and additional info
similarity_percentage, common_words_count, unique_words_count = jaccard_similarity(words1_set, words2_set)
print(f"Jaccard Similarity: {similarity_percentage:.2f}%")
print(f"Common words: {common_words_count}")
print(f"Unique words: {unique_words_count}")

# Calculate LCS and additional info
lcs_length, lcs_similarity_percentage, non_lcs_words = lcs(words1_list, words2_list)
print(f"LCS Length: {lcs_length}")
print(f"LCS Similarity: {lcs_similarity_percentage:.2f}%")
print(f"Non-LCS words: {non_lcs_words}")

# Calculate Levenshtein Distance between two strings (join words for sentence-level comparison)
lev_distance, lev_similarity, matches, differences = levenshtein_distance(' '.join(words1_list), ' '.join(words2_list))
print(f"Levenshtein Distance: {lev_distance}")
print(f"Levenshtein Similarity: {lev_similarity:.2f}%")
print(f"Matching characters: {matches}")
print(f"Differing characters: {differences}")

# Calculate Cosine Similarity
cosine_similarity_percentage, cosine_common_words, cosine_unique_words = cosine_similarity(vec1, vec2)
print(f"Cosine Similarity: {cosine_similarity_percentage:.2f}%")
print(f"Common words: {cosine_common_words}")
print(f"Unique words: {cosine_unique_words}")

# Calculate Rabin-Karp
# Calculate using Rabin-Karp
rabin_karp_common_words, rabin_karp_unique_words, rabin_karp_similarity = rabin_karp(words1_text, patterns)
print(f"Rabin-Karp Common words: {rabin_karp_common_words}")
print(f"Rabin-Karp Unique words: {rabin_karp_unique_words}")
print(f"Rabin-Karp Similarity: {rabin_karp_similarity:.2f}%")