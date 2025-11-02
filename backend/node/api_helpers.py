"""
External API helpers module for GenLayer contracts.

This module provides helper functions that are automatically injected into
contract code to enable easy access to external APIs without requiring API keys.
Contracts can use gl.api.get_weather(), gl.api.get_price(), etc.
"""

API_HELPERS_CODE = """
# ===== INJECTED: External API Module =====
import json

class APIModule:
    """API helpers for external APIs - auto-injected by GenLayer Studio."""
    
    @staticmethod
    def _fetch_json(url: str) -> dict:
        """
        Internal: Fetch JSON from external API using gl.nondet.web.request.
        
        Must be called within a non-deterministic block (gl.eq_principle.strict_eq).
        """
        def fetch():
            response = gl.nondet.web.request(
                url=url,
                method="GET",
                headers={"Content-Type": "application/json"}
            )
            return json.loads(response.body)
        return gl.eq_principle.strict_eq(fetch)
    
    # ===== WEATHER API =====
    @staticmethod
    def get_weather(city: str) -> dict:
        """
        Get weather data for a city - no API key required!
        
        Uses Open-Meteo API (free, no authentication needed).
        
        Args:
            city: City name (e.g., "London", "Paris", "Tokyo")
            
        Returns:
            dict with weather data including:
                - city: City name
                - temperature: Temperature in Celsius
                - condition: Weather condition code
                - windspeed: Wind speed in km/h
        """
        # Step 1: Get city coordinates
        geocode_url = f"https://geocoding-api.open-meteo.com/v1/search?name={city}&count=1"
        location_data = APIModule._fetch_json(geocode_url)
        
        if location_data.get("results"):
            result = location_data["results"][0]
            lat = result["latitude"]
            lon = result["longitude"]
            
            # Step 2: Get weather data
            weather_url = f"https://api.open-meteo.com/v1/forecast?latitude={lat}&longitude={lon}&current_weather=true"
            weather = APIModule._fetch_json(weather_url)
            
            return {
                "city": city,
                "temperature": weather["current_weather"]["temperature"],
                "condition": weather["current_weather"]["weathercode"],
                "windspeed": weather["current_weather"]["windspeed"],
            }
        return {"error": f"City {city} not found"}
    
    @staticmethod
    def get_weather_temperature(city: str) -> float:
        """
        Get temperature only for a city - no API key required!
        
        Args:
            city: City name
            
        Returns:
            Temperature in Celsius (float)
        """
        weather = APIModule.get_weather(city)
        return weather.get("temperature", 0.0)
    
    # ===== CRYPTOCURRENCY PRICE API =====
    @staticmethod
    def get_price(symbol: str) -> dict:
        """
        Get cryptocurrency price - no API key required!
        
        Uses CoinGecko API (free tier, no authentication needed).
        
        Args:
            symbol: Coin symbol (e.g., "BTC", "ETH", "USD")
            
        Returns:
            dict with price data including:
                - symbol: Coin symbol
                - price_usd: Price in USD
                - change_24h: 24h price change percentage
        """
        # CoinGecko API - FREE tier, no key needed for basic calls
        url = f"https://api.coingecko.com/api/v3/simple/price?ids={symbol.lower()}&vs_currencies=usd&include_24hr_change=true"
        data = APIModule._fetch_json(url)
        
        if symbol.lower() in data:
            return {
                "symbol": symbol.upper(),
                "price_usd": data[symbol.lower()]["usd"],
                "change_24h": data[symbol.lower()].get("usd_24h_change", 0)
            }
        
        # Fallback: Try with common coin IDs
        coin_map = {
            "BTC": "bitcoin",
            "ETH": "ethereum",
            "USDT": "tether",
            "BNB": "binancecoin",
            "ADA": "cardano",
            "SOL": "solana",
            "XRP": "ripple",
            "DOT": "polkadot",
            "DOGE": "dogecoin",
        }
        
        coin_id = coin_map.get(symbol.upper(), symbol.lower())
        url = f"https://api.coingecko.com/api/v3/simple/price?ids={coin_id}&vs_currencies=usd&include_24hr_change=true"
        data = APIModule._fetch_json(url)
        
        if coin_id in data:
            return {
                "symbol": symbol.upper(),
                "price_usd": data[coin_id]["usd"],
                "change_24h": data[coin_id].get("usd_24h_change", 0)
            }
        
        return {"error": f"Coin {symbol} not found"}
    
    @staticmethod
    def get_price_value(symbol: str) -> float:
        """
        Get cryptocurrency price value only - no API key required!
        
        Args:
            symbol: Coin symbol (e.g., "BTC", "ETH")
            
        Returns:
            Price in USD (float)
        """
        price_data = APIModule.get_price(symbol)
        return price_data.get("price_usd", 0.0)
    
    # ===== RANDOM DATA APIs (for testing/demo) =====
    @staticmethod
    def get_random_joke() -> str:
        """
        Get random joke - no API key required!
        
        Uses Official Joke API (free, no authentication).
        
        Returns:
            Joke string in format "setup - punchline"
        """
        url = "https://official-joke-api.appspot.com/random_joke"
        joke = APIModule._fetch_json(url)
        return f"{joke['setup']} - {joke['punchline']}"
    
    @staticmethod
    def get_random_quote() -> dict:
        """
        Get random quote - no API key required!
        
        Uses Quotable API (free, no authentication).
        
        Returns:
            dict with quote data (content, author, etc.)
        """
        url = "https://api.quotable.io/random"
        return APIModule._fetch_json(url)
    
    # ===== NEWS API =====
    @staticmethod
    def get_news(category: str = "general", limit: int = 5) -> list:
        """
        Get news articles - no API key required!
        
        Uses NewsData.io API (free tier, no authentication needed).
        
        Args:
            category: News category (e.g., "technology", "business", "sports", "general")
            limit: Number of articles to return (default: 5, max: 10)
            
        Returns:
            list of news articles with title, description, link, pubDate
        """
        # NewsData.io - free tier, no key needed
        url = f"https://newsdata.io/api/1/news?apikey=pub_00000000000000000000000000&category={category}&language=en"
        data = APIModule._fetch_json(url)
        
        if "results" in data and data["results"]:
            articles = data["results"][:limit]
            return [
                {
                    "title": article.get("title", ""),
                    "description": article.get("description", ""),
                    "link": article.get("link", ""),
                    "pubDate": article.get("pubDate", ""),
                }
                for article in articles
            ]
        return []
    
    @staticmethod
    def get_news_by_query(query: str, limit: int = 5) -> list:
        """
        Get news articles by search query - no API key required!
        
        Uses NewsData.io API (free tier).
        
        Args:
            query: Search query (e.g., "bitcoin", "ai", "climate")
            limit: Number of articles to return (default: 5, max: 10)
            
        Returns:
            list of news articles matching the query
        """
        url = f"https://newsdata.io/api/1/news?apikey=pub_00000000000000000000000000&q={query}&language=en"
        data = APIModule._fetch_json(url)
        
        if "results" in data and data["results"]:
            articles = data["results"][:limit]
            return [
                {
                    "title": article.get("title", ""),
                    "description": article.get("description", ""),
                    "link": article.get("link", ""),
                    "pubDate": article.get("pubDate", ""),
                }
                for article in articles
            ]
        return []
    
    # ===== EXCHANGE RATES API =====
    @staticmethod
    def get_exchange_rate(from_currency: str, to_currency: str = "USD") -> dict:
        """
        Get exchange rate between two currencies - no API key required!
        
        Uses ExchangeRate-API (free tier, no authentication).
        
        Args:
            from_currency: Source currency code (e.g., "EUR", "GBP", "JPY")
            to_currency: Target currency code (default: "USD")
            
        Returns:
            dict with exchange rate data:
                - from: Source currency
                - to: Target currency
                - rate: Exchange rate (float)
                - date: Rate date
        """
        url = f"https://api.exchangerate-api.com/v4/latest/{from_currency.upper()}"
        data = APIModule._fetch_json(url)
        
        if "rates" in data and to_currency.upper() in data["rates"]:
            return {
                "from": from_currency.upper(),
                "to": to_currency.upper(),
                "rate": data["rates"][to_currency.upper()],
                "date": data.get("date", ""),
            }
        return {"error": f"Exchange rate not found for {from_currency} to {to_currency}"}
    
    @staticmethod
    def get_exchange_rate_value(from_currency: str, to_currency: str = "USD") -> float:
        """
        Get exchange rate value only - no API key required!
        
        Args:
            from_currency: Source currency code (e.g., "EUR", "GBP")
            to_currency: Target currency code (default: "USD")
            
        Returns:
            Exchange rate value (float)
        """
        rate_data = APIModule.get_exchange_rate(from_currency, to_currency)
        return rate_data.get("rate", 0.0)
    
    # ===== TIME/DATE API =====
    @staticmethod
    def get_current_time(timezone: str = "UTC") -> dict:
        """
        Get current time for a timezone - no API key required!
        
        Uses TimeAPI (free, no authentication).
        
        Args:
            timezone: Timezone identifier (e.g., "UTC", "America/New_York", "Europe/London")
            
        Returns:
            dict with time data:
                - datetime: ISO datetime string
                - timezone: Timezone name
                - day_of_week: Day name
        """
        url = f"https://timeapi.io/api/Time/current/zone?timeZone={timezone}"
        data = APIModule._fetch_json(url)
        
        if "dateTime" in data:
            return {
                "datetime": data.get("dateTime", ""),
                "timezone": data.get("timeZone", ""),
                "day_of_week": data.get("dayOfWeek", ""),
            }
        return {"error": f"Timezone {timezone} not found"}
    
    @staticmethod
    def get_current_date() -> str:
        """
        Get current date in ISO format - no API key required!
        
        Returns:
            Current date string (YYYY-MM-DD)
        """
        time_data = APIModule.get_current_time("UTC")
        if "datetime" in time_data:
            return time_data["datetime"][:10]  # Extract date part
        return ""
    
    # ===== STOCK PRICE API (limited) =====
    @staticmethod
    def get_stock_price(symbol: str) -> dict:
        """
        Get stock price - no API key required!
        
        Uses Finnhub API (free tier, no authentication for basic calls).
        Note: Limited requests per minute on free tier.
        
        Args:
            symbol: Stock symbol (e.g., "AAPL", "GOOGL", "MSFT")
            
        Returns:
            dict with stock data:
                - symbol: Stock symbol
                - price: Current price
                - change: Price change
                - percent_change: Percent change
        """
        # Using Finnhub free tier - basic quote endpoint
        # Note: This may require API key in production, but free tier allows some calls
        url = f"https://finnhub.io/api/v1/quote?symbol={symbol.upper()}"
        data = APIModule._fetch_json(url)
        
        if "c" in data and data["c"]:  # 'c' is current price
            return {
                "symbol": symbol.upper(),
                "price": data.get("c", 0.0),
                "change": data.get("d", 0.0),  # Change value
                "percent_change": data.get("dp", 0.0),  # Percent change
            }
        return {"error": f"Stock {symbol} not found or rate limited"}
    
    @staticmethod
    def get_stock_price_value(symbol: str) -> float:
        """
        Get stock price value only - no API key required!
        
        Args:
            symbol: Stock symbol (e.g., "AAPL", "GOOGL")
            
        Returns:
            Stock price (float)
        """
        stock_data = APIModule.get_stock_price(symbol)
        return stock_data.get("price", 0.0)
    
    # ===== IP GEOLOCATION API =====
    @staticmethod
    def get_ip_location(ip: str = None) -> dict:
        """
        Get location data for an IP address - no API key required!
        
        Uses ip-api.com (free tier, no authentication).
        
        Args:
            ip: IP address (optional, defaults to request origin)
            
        Returns:
            dict with location data:
                - country: Country name
                - city: City name
                - lat: Latitude
                - lon: Longitude
                - timezone: Timezone
        """
        if ip:
            url = f"http://ip-api.com/json/{ip}?fields=status,country,city,lat,lon,timezone"
        else:
            url = "http://ip-api.com/json/?fields=status,country,city,lat,lon,timezone"
        
        data = APIModule._fetch_json(url)
        
        if data.get("status") == "success":
            return {
                "country": data.get("country", ""),
                "city": data.get("city", ""),
                "lat": data.get("lat", 0.0),
                "lon": data.get("lon", 0.0),
                "timezone": data.get("timezone", ""),
            }
        return {"error": "IP location not found"}

# Inject into gl namespace
if 'gl' in globals():
    gl.api = APIModule()
# ===== END INJECTED =====
"""


def inject_api_module(contract_code: bytes) -> bytes:
    """
    Inject API helpers into contract code.
    
    Adds gl.api module after 'from genlayer import *' line.
    This allows contracts to use gl.api.get_weather(), gl.api.get_price(), etc.
    
    Args:
        contract_code: Contract code as bytes
        
    Returns:
        Contract code with API helpers injected (as bytes)
    """
    try:
        code_str = contract_code.decode('utf-8')
    except UnicodeDecodeError:
        # If can't decode, return as-is
        return contract_code
    
    # Only inject if contract uses genlayer
    if "from genlayer import *" not in code_str:
        return contract_code
    
    # Check if already injected (avoid double injection)
    if "gl.api = APIModule()" in code_str:
        return contract_code
    
    # Inject after "from genlayer import *"
    code_str = code_str.replace(
        "from genlayer import *",
        f"from genlayer import *\n\n{API_HELPERS_CODE}"
    )
    
    return code_str.encode('utf-8')

