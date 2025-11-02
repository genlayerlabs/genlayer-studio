# v0.1.0
# { "Depends": "py-genlayer:latest" }

from genlayer import *
import json

# gl.api is automatically injected by GenLayer Studio
# You can use gl.api functions without any setup or API keys!
#
# Available APIs:
# - Weather: gl.api.get_weather(), gl.api.get_weather_temperature()
# - Crypto: gl.api.get_price(), gl.api.get_price_value()
# - News: gl.api.get_news(), gl.api.get_news_by_query()
# - Exchange: gl.api.get_exchange_rate(), gl.api.get_exchange_rate_value()
# - Time: gl.api.get_current_time(), gl.api.get_current_date()
# - Stock: gl.api.get_stock_price(), gl.api.get_stock_price_value()
# - Location: gl.api.get_ip_location()


# ===== WEATHER ORACLE =====
class WeatherOracle(gl.Contract):
    """Example contract using gl.api for weather data - no API key needed!"""
    
    temperature_data: TreeMap[str, u256]
    weather_data: TreeMap[str, str]
    
    def __init__(self):
        pass
    
    @gl.public.write
    def update_weather(self, city: str) -> None:
        """
        Update weather data for a city.
        
        Uses gl.api.get_weather() - just pass the city name!
        No API key required.
        """
        weather = gl.api.get_weather(city)
        
        if "error" not in weather:
            # Store temperature as u256 (multiply by 100 to preserve 2 decimal places)
            temp_celsius = int(weather.get("temperature", 0.0) * 100)
            self.temperature_data[city] = u256(temp_celsius)
            # Store weather data as JSON string
            self.weather_data[city] = json.dumps(weather)
    
    @gl.public.write
    def update_temperature_only(self, city: str) -> None:
        """
        Update only temperature for a city.
        
        Uses gl.api.get_weather_temperature() - just pass the city name!
        """
        temp = gl.api.get_weather_temperature(city)
        # Store temperature as u256 (multiply by 100 to preserve 2 decimal places)
        temp_int = int(temp * 100)
        self.temperature_data[city] = u256(temp_int)
    
    @gl.public.view
    def get_temperature(self, city: str) -> int:
        """Get cached temperature for a city (returns value * 100 to preserve decimals)."""
        temp_int = self.temperature_data.get(city, u256(0))
        return temp_int
    
    @gl.public.view
    def get_weather_info(self, city: str) -> str:
        """Get cached weather info for a city as JSON string."""
        return self.weather_data.get(city, "{}")


# ===== PRICE ORACLE =====
class PriceOracle(gl.Contract):
    """Example contract using gl.api for cryptocurrency prices - no API key needed!"""
    
    price_data: TreeMap[str, u256]
    price_history: TreeMap[str, str]
    
    def __init__(self):
        pass
    
    @gl.public.write
    def update_price(self, symbol: str) -> None:
        """
        Update price for a cryptocurrency.
        
        Uses gl.api.get_price() - just pass the coin symbol (e.g., "BTC", "ETH")!
        No API key required.
        """
        price_info = gl.api.get_price(symbol)
        
        if "error" not in price_info:
            # Store price as u256 (multiply by 1e18 to preserve precision)
            price_usd = price_info.get("price_usd", 0.0)
            price_wei = int(price_usd * 1e18)
            self.price_data[symbol] = u256(price_wei)
            # Store price history as JSON string
            self.price_history[symbol] = json.dumps(price_info)
    
    @gl.public.write
    def update_price_value_only(self, symbol: str) -> None:
        """
        Update only price value.
        
        Uses gl.api.get_price_value() - just pass the coin symbol!
        """
        price = gl.api.get_price_value(symbol)
        # Store price as u256 (multiply by 1e18 to preserve precision)
        price_wei = int(price * 1e18)
        self.price_data[symbol] = u256(price_wei)
    
    @gl.public.view
    def get_price(self, symbol: str) -> int:
        """Get cached price for a cryptocurrency (returns value in wei: price * 1e18)."""
        return self.price_data.get(symbol, u256(0))
    
    @gl.public.view
    def get_price_info(self, symbol: str) -> str:
        """Get cached price info including 24h change as JSON string."""
        return self.price_history.get(symbol, "{}")


# ===== NEWS ORACLE =====
class NewsOracle(gl.Contract):
    """Example contract using gl.api for news articles - no API key needed!"""
    
    news_cache: TreeMap[str, str]
    last_updated: TreeMap[str, str]
    
    def __init__(self):
        pass
    
    @gl.public.write
    def update_news(self, category: str) -> None:
        """
        Update news articles for a category.
        
        Uses gl.api.get_news() - just pass the category!
        Categories: technology, business, sports, general, etc.
        """
        news = gl.api.get_news(category, limit=5)
        if news:
            # Store news as JSON string
            self.news_cache[category] = json.dumps(news)
            # Fetch and cache current date
            current_date = gl.api.get_current_date()
            self.last_updated[category] = current_date
    
    @gl.public.write
    def search_news(self, query: str) -> None:
        """
        Search news by query.
        
        Uses gl.api.get_news_by_query() - just pass the search term!
        """
        news = gl.api.get_news_by_query(query, limit=5)
        if news:
            # Store news as JSON string
            self.news_cache[f"query_{query}"] = json.dumps(news)
            # Fetch and cache current date
            current_date = gl.api.get_current_date()
            self.last_updated[f"query_{query}"] = current_date
    
    @gl.public.view
    def get_news(self, key: str) -> str:
        """Get cached news articles as JSON string."""
        return self.news_cache.get(key, "[]")
    
    @gl.public.view
    def get_last_update(self, key: str) -> str:
        """Get last update date for a news category."""
        return self.last_updated.get(key, "")


# ===== EXCHANGE RATE ORACLE =====
class ExchangeRateOracle(gl.Contract):
    """Example contract using gl.api for exchange rates - no API key needed!"""
    
    rates: TreeMap[str, u256]
    
    def __init__(self):
        pass
    
    @gl.public.write
    def update_rate(self, from_currency: str, to_currency: str = "USD") -> None:
        """
        Update exchange rate.
        
        Uses gl.api.get_exchange_rate() - just pass currency codes!
        Example: update_rate("EUR", "USD") for EUR to USD
        """
        rate_data = gl.api.get_exchange_rate(from_currency, to_currency)
        if "error" not in rate_data:
            key = f"{from_currency}_{to_currency}"
            # Store rate as u256 (multiply by 1e18 to preserve precision)
            rate = rate_data.get("rate", 0.0)
            rate_wei = int(rate * 1e18)
            self.rates[key] = u256(rate_wei)
    
    @gl.public.view
    def get_rate(self, from_currency: str, to_currency: str = "USD") -> int:
        """Get cached exchange rate (returns value in wei: rate * 1e18)."""
        key = f"{from_currency}_{to_currency}"
        return self.rates.get(key, u256(0))


# ===== COMBINED ORACLE (Multiple APIs) =====
class CombinedOracle(gl.Contract):
    """Example contract using multiple gl.api functions together."""
    
    data: TreeMap[str, str]
    cached_date: str
    cached_time: TreeMap[str, str]
    
    def __init__(self):
        pass
    
    @gl.public.write
    def update_city_weather(self, city: str) -> None:
        """Update weather for a city."""
        weather = gl.api.get_weather(city)
        self.data[f"weather_{city}"] = json.dumps(weather)
    
    @gl.public.write
    def update_coin_price(self, symbol: str) -> None:
        """Update price for a coin."""
        price = gl.api.get_price(symbol)
        self.data[f"price_{symbol}"] = json.dumps(price)
    
    @gl.public.write
    def update_exchange_rate(self, from_curr: str, to_curr: str = "USD") -> None:
        """Update exchange rate."""
        rate = gl.api.get_exchange_rate(from_curr, to_curr)
        if "error" not in rate:
            self.data[f"rate_{from_curr}_{to_curr}"] = json.dumps(rate)
    
    @gl.public.write
    def update_news_category(self, category: str) -> None:
        """Update news for a category."""
        news = gl.api.get_news(category, limit=3)
        result = {"articles": news, "count": len(news)}
        self.data[f"news_{category}"] = json.dumps(result)
    
    @gl.public.write
    def update_stock_price(self, symbol: str) -> None:
        """Update stock price."""
        stock = gl.api.get_stock_price(symbol)
        if "error" not in stock:
            self.data[f"stock_{symbol}"] = json.dumps(stock)
    
    @gl.public.write
    def update_current_date(self) -> None:
        """Fetch and cache current date using gl.api."""
        self.cached_date = gl.api.get_current_date()
    
    @gl.public.write
    def update_current_time(self, timezone: str = "UTC") -> None:
        """Fetch and cache current time for a timezone using gl.api."""
        time_data = gl.api.get_current_time(timezone)
        self.cached_time[timezone] = json.dumps(time_data)
    
    @gl.public.view
    def get_data(self, key: str) -> str:
        """Get any stored data as JSON string."""
        return self.data.get(key, "{}")
    
    @gl.public.view
    def get_current_date(self) -> str:
        """Get cached current date."""
        return self.cached_date
    
    @gl.public.view
    def get_current_time(self, timezone: str = "UTC") -> str:
        """Get cached current time for a timezone as JSON string."""
        return self.cached_time.get(timezone, "{}")


# ===== STOCK PRICE ORACLE =====
class StockOracle(gl.Contract):
    """Example contract using gl.api for stock prices - no API key needed!"""
    
    stock_data: TreeMap[str, str]
    
    def __init__(self):
        pass
    
    @gl.public.write
    def update_stock(self, symbol: str) -> None:
        """
        Update stock price.
        
        Uses gl.api.get_stock_price() - just pass the stock symbol (e.g., "AAPL", "GOOGL")!
        """
        stock_info = gl.api.get_stock_price(symbol)
        if "error" not in stock_info:
            self.stock_data[symbol] = json.dumps(stock_info)
    
    @gl.public.view
    def get_stock(self, symbol: str) -> str:
        """Get cached stock data as JSON string."""
        return self.stock_data.get(symbol, "{}")


# ===== TIME/DATE ORACLE =====
class TimeOracle(gl.Contract):
    """Example contract using gl.api for time/date information."""
    
    timezone_data: TreeMap[str, str]
    cached_date: str
    
    def __init__(self):
        pass
    
    @gl.public.write
    def update_timezone(self, timezone: str) -> None:
        """
        Update time for a timezone.
        
        Uses gl.api.get_current_time() - just pass the timezone!
        Example: "UTC", "America/New_York", "Europe/London"
        """
        time_data = gl.api.get_current_time(timezone)
        if "error" not in time_data:
            self.timezone_data[timezone] = json.dumps(time_data)
    
    @gl.public.write
    def update_current_date(self) -> None:
        """Fetch and cache current date using gl.api."""
        self.cached_date = gl.api.get_current_date()
    
    @gl.public.view
    def get_timezone(self, timezone: str) -> str:
        """Get cached timezone data as JSON string."""
        return self.timezone_data.get(timezone, "{}")
    
    @gl.public.view
    def get_current_date(self) -> str:
        """Get cached current date."""
        return self.cached_date

