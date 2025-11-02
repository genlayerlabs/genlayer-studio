"""
External API helpers module for GenLayer contracts.

This module provides helper functions that are automatically injected into
contract code to enable easy access to external APIs without requiring API keys.
Contracts can use gl.api.get_weather(), gl.api.get_price(), etc.
"""

API_HELPERS_CODE = """
# ===== INJECTED: External API Module =====
import json
import re
from urllib.parse import urlparse

class APIModule:
    """API helpers for external APIs - auto-injected by GenLayer Studio."""
    
    # Whitelist of allowed API domains for security
    _ALLOWED_API_DOMAINS = {
        "api.open-meteo.com",
        "geocoding-api.open-meteo.com",
        "api.coingecko.com",
        "newsdata.io",
        "api.exchangerate-api.com",
        "timeapi.io",
        "finnhub.io",
        "ip-api.com",
        "official-joke-api.appspot.com",
        "api.quotable.io",
    }
    
    # Maximum allowed response size (1MB) to prevent DoS attacks
    _MAX_RESPONSE_SIZE = 1_000_000
    
    @staticmethod
    def _validate_string_input(value: str, max_length: int = 100, pattern: str = None) -> str:
        """
        Validate and sanitize string inputs to prevent injection attacks.
        
        Args:
            value: Input string to validate
            max_length: Maximum allowed length
            pattern: Optional regex pattern for validation
            
        Returns:
            Sanitized string
            
        Raises:
            ValueError: If validation fails
        """
        if not isinstance(value, str):
            raise ValueError(f"Expected string, got {type(value)}")
        
        # Check length
        if len(value) > max_length:
            raise ValueError(f"Input too long: max {max_length} characters")
        
        # Check empty
        value = value.strip()
        if not value:
            raise ValueError("Input cannot be empty")
        
        # Remove null bytes and dangerous characters
        value = value.replace('\x00', '').replace('\r', '').replace('\n', '')
        
        # Pattern validation
        if pattern and not re.match(pattern, value):
            raise ValueError("Input contains invalid characters")
        
        return value
    
    @staticmethod
    def _validate_url(url: str) -> bool:
        """
        Validate URL is from allowed domains and safe format.
        
        Args:
            url: URL to validate
            
        Returns:
            True if URL is safe, False otherwise
        """
        try:
            parsed = urlparse(url)
            domain = parsed.netloc.lower()
            
            # Check domain whitelist
            if domain not in APIModule._ALLOWED_API_DOMAINS:
                return False
            
            # Only allow HTTPS (except ip-api.com which uses HTTP)
            if domain != "ip-api.com" and parsed.scheme != "https":
                return False
            
            # Prevent path traversal attacks
            if ".." in parsed.path or "//" in parsed.path:
                return False
            
            # Prevent query parameter injection patterns
            if parsed.query and ("<" in parsed.query or ">" in parsed.query or "{" in parsed.query):
                return False
            
            return True
        except Exception:
            return False
    
    @staticmethod
    def _safe_json_loads(json_str: str) -> dict:
        """
        Safely parse JSON with size limits and error handling.
        
        Args:
            json_str: JSON string to parse
            
        Returns:
            Parsed dict or error dict
        """
        try:
            if not isinstance(json_str, str):
                return {"error": "Invalid response type"}
            
            # Size limit to prevent DoS
            if len(json_str) > APIModule._MAX_RESPONSE_SIZE:
                return {"error": "Response too large"}
            
            data = json.loads(json_str)
            
            # Ensure result is a dict
            if not isinstance(data, dict):
                return {"error": "Invalid JSON structure: expected object"}
            
            return data
        except json.JSONDecodeError as e:
            return {"error": f"JSON parse error: {str(e)}"}
        except Exception as e:
            return {"error": f"Unexpected error: {str(e)}"}
    
    @staticmethod
    def _fetch_json(url: str) -> dict:
        """
        Internal: Fetch JSON from external API with security validation.
        
        Must be called within a non-deterministic block (gl.eq_principle.strict_eq).
        
        Args:
            url: URL to fetch (must be from whitelisted domains)
            
        Returns:
            Parsed JSON dict or error dict
        """
        # Validate URL before making request
        if not APIModule._validate_url(url):
            return {"error": "Invalid or unauthorized URL"}
        
        def fetch():
            try:
                response = gl.nondet.web.request(
                    url=url,
                    method="GET",
                    headers={"Content-Type": "application/json"}
                )
                
                # Validate response structure
                if not hasattr(response, 'body'):
                    return {"error": "Invalid response format"}
                
                if not isinstance(response.body, str):
                    return {"error": "Response body must be string"}
                
                # Safe JSON parsing
                return APIModule._safe_json_loads(response.body)
            except Exception as e:
                return {"error": f"Request failed: {str(e)}"}
        
        result = gl.eq_principle.strict_eq(fetch)
        
        # Ensure result is always a dict
        if not isinstance(result, dict):
            return {"error": "Invalid API response format"}
        
        return result
    
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
        # Validate input
        try:
            city = APIModule._validate_string_input(
                city,
                max_length=50,
                pattern=r"^[a-zA-Z0-9\s\-\']+$"
            )
        except ValueError as e:
            return {"error": f"Invalid city name: {str(e)}"}
        
        # URL encode city name for safety
        try:
            from urllib.parse import quote
            city_encoded = quote(city)
        except Exception:
            return {"error": "Failed to encode city name"}
        
        # Step 1: Get city coordinates
        geocode_url = f"https://geocoding-api.open-meteo.com/v1/search?name={city_encoded}&count=1"
        location_data = APIModule._fetch_json(geocode_url)
        
        if "error" in location_data:
            return location_data
        
        if location_data.get("results") and len(location_data["results"]) > 0:
            result = location_data["results"][0]
            
            # Validate response structure
            if "latitude" not in result or "longitude" not in result:
                return {"error": "Invalid location data response"}
            
            try:
                lat = float(result["latitude"])
                lon = float(result["longitude"])
                
                # Validate coordinate ranges
                if not (-90 <= lat <= 90) or not (-180 <= lon <= 180):
                    return {"error": "Invalid coordinates"}
            except (ValueError, TypeError):
                return {"error": "Invalid coordinate format"}
            
            # Step 2: Get weather data
            weather_url = f"https://api.open-meteo.com/v1/forecast?latitude={lat}&longitude={lon}&current_weather=true"
            weather = APIModule._fetch_json(weather_url)
            
            if "error" in weather:
                return weather
            
            # Validate weather response structure
            if "current_weather" not in weather:
                return {"error": "Invalid weather response structure"}
            
            current = weather["current_weather"]
            required_fields = ["temperature", "weathercode", "windspeed"]
            
            for field in required_fields:
                if field not in current:
                    return {"error": f"Missing weather field: {field}"}
            
            return {
                "city": city,
                "temperature": float(current["temperature"]) if current.get("temperature") is not None else 0.0,
                "condition": int(current["weathercode"]) if current.get("weathercode") is not None else 0,
                "windspeed": float(current["windspeed"]) if current.get("windspeed") is not None else 0.0,
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
        
        if "error" in weather:
            return 0.0
        
        try:
            temp = weather.get("temperature", 0.0)
            return float(temp) if temp is not None else 0.0
        except (ValueError, TypeError):
            return 0.0
    
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
        # Validate input
        try:
            symbol = APIModule._validate_string_input(
                symbol.upper(),
                max_length=10,
                pattern=r"^[A-Z0-9]+$"
            )
        except ValueError as e:
            return {"error": f"Invalid symbol: {str(e)}"}
        
        # Coin map for common coins (whitelist approach)
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
            "MATIC": "matic-network",
            "LTC": "litecoin",
            "AVAX": "avalanche-2",
            "LINK": "chainlink",
            "UNI": "uniswap",
        }
        
        # Use whitelisted coin ID if available
        coin_id = coin_map.get(symbol, symbol.lower())
        
        # Validate coin_id length
        if len(coin_id) > 50:
            return {"error": "Coin ID too long"}
        
        # URL encode coin_id
        try:
            from urllib.parse import quote
            coin_id_encoded = quote(coin_id)
        except Exception:
            return {"error": "Failed to encode coin ID"}
        
        url = f"https://api.coingecko.com/api/v3/simple/price?ids={coin_id_encoded}&vs_currencies=usd&include_24hr_change=true"
        data = APIModule._fetch_json(url)
        
        if "error" in data:
            return data
        
        if coin_id in data:
            coin_data = data[coin_id]
            
            # Validate response structure
            if not isinstance(coin_data, dict) or "usd" not in coin_data:
                return {"error": "Invalid price response structure"}
            
            try:
                price_usd = float(coin_data["usd"]) if coin_data.get("usd") is not None else 0.0
                change_24h = float(coin_data.get("usd_24h_change", 0)) if coin_data.get("usd_24h_change") is not None else 0.0
                
                return {
                    "symbol": symbol,
                    "price_usd": price_usd,
                    "change_24h": change_24h
                }
            except (ValueError, TypeError):
                return {"error": "Invalid price data format"}
        
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
        
        if "error" in price_data:
            return 0.0
        
        try:
            price = price_data.get("price_usd", 0.0)
            return float(price) if price is not None else 0.0
        except (ValueError, TypeError):
            return 0.0
    
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
        
        if "error" in joke:
            return ""
        
        # Validate response structure
        if not isinstance(joke, dict) or "setup" not in joke or "punchline" not in joke:
            return ""
        
        # Sanitize output
        setup = str(joke.get("setup", "")).strip()[:500]  # Limit length
        punchline = str(joke.get("punchline", "")).strip()[:500]
        
        # Remove potentially dangerous characters
        setup = setup.replace("<", "").replace(">", "").replace("{", "").replace("}", "")
        punchline = punchline.replace("<", "").replace(">", "").replace("{", "").replace("}", "")
        
        return f"{setup} - {punchline}"
    
    @staticmethod
    def get_random_quote() -> dict:
        """
        Get random quote - no API key required!
        
        Uses Quotable API (free, no authentication).
        
        Returns:
            dict with quote data (content, author, etc.)
        """
        url = "https://api.quotable.io/random"
        quote_data = APIModule._fetch_json(url)
        
        if "error" in quote_data:
            return quote_data
        
        # Sanitize string fields
        if isinstance(quote_data, dict):
            for key in ["content", "author", "tags"]:
                if key in quote_data and isinstance(quote_data[key], str):
                    # Limit length and sanitize
                    value = quote_data[key]
                    if len(value) > 1000:
                        value = value[:1000]
                    quote_data[key] = value.replace("\x00", "")
        
        return quote_data
    
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
        # Validate category
        valid_categories = {"technology", "business", "sports", "general", "health", "science", "entertainment"}
        try:
            category = APIModule._validate_string_input(
                category.lower(),
                max_length=20,
                pattern=r"^[a-z]+$"
            )
        except ValueError:
            return []
        
        if category not in valid_categories:
            return []
        
        # Validate limit
        try:
            limit = int(limit) if limit else 5
            limit = max(1, min(10, limit))  # Clamp between 1 and 10
        except (ValueError, TypeError):
            limit = 5
        
        # URL encode category
        try:
            from urllib.parse import quote
            category_encoded = quote(category)
        except Exception:
            return []
        
        url = f"https://newsdata.io/api/1/news?apikey=pub_00000000000000000000000000&category={category_encoded}&language=en"
        data = APIModule._fetch_json(url)
        
        if "error" in data:
            return []
        
        if "results" in data and isinstance(data["results"], list):
            articles = data["results"][:limit]
            
            # Sanitize article data
            sanitized_articles = []
            for article in articles:
                if not isinstance(article, dict):
                    continue
                
                # Sanitize and limit string fields
                title = str(article.get("title", ""))[:500] if article.get("title") else ""
                description = str(article.get("description", ""))[:1000] if article.get("description") else ""
                link = str(article.get("link", ""))[:500] if article.get("link") else ""
                pubDate = str(article.get("pubDate", ""))[:50] if article.get("pubDate") else ""
                
                # Remove dangerous characters from each field
                dangerous_chars = ["<", ">", "{", "}"]
                for char in dangerous_chars:
                    title = title.replace(char, "")
                    description = description.replace(char, "")
                    link = link.replace(char, "")
                    pubDate = pubDate.replace(char, "")
                
                sanitized_articles.append({
                    "title": title.replace("\x00", ""),
                    "description": description.replace("\x00", ""),
                    "link": link.replace("\x00", ""),
                    "pubDate": pubDate.replace("\x00", ""),
                })
            
            return sanitized_articles
        
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
        # Validate query
        try:
            query = APIModule._validate_string_input(
                query,
                max_length=100,
                pattern=r"^[a-zA-Z0-9\s\-\']+$"
            )
        except ValueError:
            return []
        
        # Validate limit
        try:
            limit = int(limit) if limit else 5
            limit = max(1, min(10, limit))  # Clamp between 1 and 10
        except (ValueError, TypeError):
            limit = 5
        
        # URL encode query
        try:
            from urllib.parse import quote
            query_encoded = quote(query)
        except Exception:
            return []
        
        url = f"https://newsdata.io/api/1/news?apikey=pub_00000000000000000000000000&q={query_encoded}&language=en"
        data = APIModule._fetch_json(url)
        
        if "error" in data:
            return []
        
        if "results" in data and isinstance(data["results"], list):
            articles = data["results"][:limit]
            
            # Sanitize article data (same as get_news)
            sanitized_articles = []
            for article in articles:
                if not isinstance(article, dict):
                    continue
                
                title = str(article.get("title", ""))[:500] if article.get("title") else ""
                description = str(article.get("description", ""))[:1000] if article.get("description") else ""
                link = str(article.get("link", ""))[:500] if article.get("link") else ""
                pubDate = str(article.get("pubDate", ""))[:50] if article.get("pubDate") else ""
                
                sanitized_articles.append({
                    "title": title.replace("\x00", ""),
                    "description": description.replace("\x00", ""),
                    "link": link.replace("\x00", ""),
                    "pubDate": pubDate.replace("\x00", ""),
                })
            
            return sanitized_articles
        
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
        # Validate currency codes
        try:
            from_currency = APIModule._validate_string_input(
                from_currency.upper(),
                max_length=10,
                pattern=r"^[A-Z]{3}$"
            )
            to_currency = APIModule._validate_string_input(
                to_currency.upper(),
                max_length=10,
                pattern=r"^[A-Z]{3}$"
            )
        except ValueError as e:
            return {"error": f"Invalid currency code: {str(e)}"}
        
        url = f"https://api.exchangerate-api.com/v4/latest/{from_currency}"
        data = APIModule._fetch_json(url)
        
        if "error" in data:
            return data
        
        if "rates" in data and isinstance(data["rates"], dict) and to_currency in data["rates"]:
            try:
                rate = float(data["rates"][to_currency]) if data["rates"][to_currency] is not None else 0.0
                date = str(data.get("date", ""))[:20] if data.get("date") else ""
                
                # Validate rate is reasonable (between 0.0001 and 100000)
                if not (0.0001 <= rate <= 100000):
                    return {"error": "Invalid exchange rate value"}
                
                return {
                    "from": from_currency,
                    "to": to_currency,
                    "rate": rate,
                    "date": date.replace("\x00", ""),
                }
            except (ValueError, TypeError):
                return {"error": "Invalid exchange rate format"}
        
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
        
        if "error" in rate_data:
            return 0.0
        
        try:
            rate = rate_data.get("rate", 0.0)
            return float(rate) if rate is not None else 0.0
        except (ValueError, TypeError):
            return 0.0
    
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
        # Validate timezone
        try:
            timezone = APIModule._validate_string_input(
                timezone,
                max_length=50,
                pattern=r"^[a-zA-Z0-9_/]+$"
            )
        except ValueError as e:
            return {"error": f"Invalid timezone: {str(e)}"}
        
        # URL encode timezone
        try:
            from urllib.parse import quote
            timezone_encoded = quote(timezone)
        except Exception:
            return {"error": "Failed to encode timezone"}
        
        url = f"https://timeapi.io/api/Time/current/zone?timeZone={timezone_encoded}"
        data = APIModule._fetch_json(url)
        
        if "error" in data:
            return data
        
        if "dateTime" in data:
            # Sanitize and limit string fields
            datetime_str = str(data.get("dateTime", ""))[:50] if data.get("dateTime") else ""
            timezone_str = str(data.get("timeZone", ""))[:50] if data.get("timeZone") else ""
            day_str = str(data.get("dayOfWeek", ""))[:20] if data.get("dayOfWeek") else ""
            
            return {
                "datetime": datetime_str.replace("\x00", ""),
                "timezone": timezone_str.replace("\x00", ""),
                "day_of_week": day_str.replace("\x00", ""),
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
        # Validate symbol
        try:
            symbol = APIModule._validate_string_input(
                symbol.upper(),
                max_length=10,
                pattern=r"^[A-Z0-9]+$"
            )
        except ValueError as e:
            return {"error": f"Invalid stock symbol: {str(e)}"}
        
        # URL encode symbol
        try:
            from urllib.parse import quote
            symbol_encoded = quote(symbol)
        except Exception:
            return {"error": "Failed to encode symbol"}
        
        url = f"https://finnhub.io/api/v1/quote?symbol={symbol_encoded}"
        data = APIModule._fetch_json(url)
        
        if "error" in data:
            return data
        
        if "c" in data and data["c"] is not None:  # 'c' is current price
            try:
                price = float(data.get("c", 0.0)) if data.get("c") is not None else 0.0
                change = float(data.get("d", 0.0)) if data.get("d") is not None else 0.0
                percent_change = float(data.get("dp", 0.0)) if data.get("dp") is not None else 0.0
                
                # Validate reasonable price range
                if not (0 <= price <= 1_000_000):
                    return {"error": "Invalid stock price value"}
                
                return {
                    "symbol": symbol,
                    "price": price,
                    "change": change,
                    "percent_change": percent_change,
                }
            except (ValueError, TypeError):
                return {"error": "Invalid stock data format"}
        
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
        
        if "error" in stock_data:
            return 0.0
        
        try:
            price = stock_data.get("price", 0.0)
            return float(price) if price is not None else 0.0
        except (ValueError, TypeError):
            return 0.0
    
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
            # Validate IP address format
            try:
                ip = APIModule._validate_string_input(ip, max_length=50)
                
                # Basic IPv4 validation
                import re
                ipv4_pattern = r'^(\d{1,3}\.){3}\d{1,3}$'
                if not re.match(ipv4_pattern, ip):
                    return {"error": "Invalid IP address format"}
                
                # Validate each octet is 0-255
                parts = ip.split('.')
                if len(parts) != 4:
                    return {"error": "Invalid IP address format"}
                
                for part in parts:
                    try:
                        num = int(part)
                        if not (0 <= num <= 255):
                            return {"error": "Invalid IP address range"}
                    except ValueError:
                        return {"error": "Invalid IP address format"}
                
            except ValueError as e:
                return {"error": f"Invalid IP: {str(e)}"}
            
            url = f"http://ip-api.com/json/{ip}?fields=status,country,city,lat,lon,timezone"
        else:
            url = "http://ip-api.com/json/?fields=status,country,city,lat,lon,timezone"
        
        data = APIModule._fetch_json(url)
        
        if "error" in data:
            return data
        
        if data.get("status") == "success":
            try:
                # Validate and sanitize response
                country = str(data.get("country", ""))[:100] if data.get("country") else ""
                city = str(data.get("city", ""))[:100] if data.get("city") else ""
                timezone = str(data.get("timezone", ""))[:50] if data.get("timezone") else ""
                
                # Validate coordinates
                lat = data.get("lat")
                lon = data.get("lon")
                
                if lat is not None:
                    lat = float(lat)
                    if not (-90 <= lat <= 90):
                        return {"error": "Invalid latitude"}
                else:
                    lat = 0.0
                
                if lon is not None:
                    lon = float(lon)
                    if not (-180 <= lon <= 180):
                        return {"error": "Invalid longitude"}
                else:
                    lon = 0.0
                
                return {
                    "country": country.replace("\x00", ""),
                    "city": city.replace("\x00", ""),
                    "lat": lat,
                    "lon": lon,
                    "timezone": timezone.replace("\x00", ""),
                }
            except (ValueError, TypeError) as e:
                return {"error": f"Invalid location data: {str(e)}"}
        
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

