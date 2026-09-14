"""测试 validators。"""

from alphaforge.utils.validators import (
    get_board,
    is_valid_code,
    is_valid_price,
    is_valid_qty,
    normalize_code,
)


class TestNormalizeCode:
    def test_pure_sh_main(self):
        assert normalize_code("600519") == "600519.SH"

    def test_pure_sz_main(self):
        assert normalize_code("000001") == "000001.SZ"

    def test_gem(self):
        assert normalize_code("300750") == "300750.SZ"

    def test_star(self):
        assert normalize_code("688981") == "688981.SH"

    def test_with_suffix_unchanged(self):
        assert normalize_code("600519.SH") == "600519.SH"

    def test_strips_and_upper(self):
        assert normalize_code(" 600519.sh ") == "600519.SH"


class TestGetBoard:
    def test_main(self):
        assert get_board("600519.SH") == "MAIN"
        assert get_board("000001.SZ") == "MAIN"

    def test_gem(self):
        assert get_board("300750.SZ") == "GEM"

    def test_star(self):
        assert get_board("688981.SH") == "STAR"


class TestValidation:
    def test_valid_code(self):
        assert is_valid_code("600519.SH")
        assert is_valid_code("000001")
        assert not is_valid_code("abc")

    def test_valid_qty(self):
        assert is_valid_qty(100)
        assert not is_valid_qty(0)
        assert not is_valid_qty(-1)

    def test_valid_price(self):
        assert is_valid_price(10.5)
        assert not is_valid_price(0)
        assert not is_valid_price(-1)
        assert not is_valid_price("abc")