import unittest

from app.admin_auth import hash_password, verify_password


class ProductionSecurityTests(unittest.TestCase):
    def test_admin_password_is_hashed_and_verifiable(self):
        encoded = hash_password("correct horse battery staple")
        self.assertTrue(encoded.startswith("pbkdf2_sha256$"))
        self.assertTrue(verify_password("correct horse battery staple", encoded))
        self.assertFalse(verify_password("wrong password", encoded))
        self.assertNotIn("correct horse battery staple", encoded)

    def test_short_admin_password_is_rejected(self):
        with self.assertRaises(ValueError):
            hash_password("too-short")


if __name__ == "__main__":
    unittest.main()
