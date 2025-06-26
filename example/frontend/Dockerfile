FROM node:22-alpine

WORKDIR /app

# Copy package files
COPY . .

# inject env var - use localhost:8000 since the browser needs to access it
RUN echo "VITE_BACKEND_URL=http://localhost" >> .env.production

# Build the app
RUN npm run build

# Expose port
EXPOSE 4173

# Start the preview server
CMD ["npm", "run", "preview", "--", "--host", "0.0.0.0"]
