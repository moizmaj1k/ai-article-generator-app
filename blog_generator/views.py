from django.shortcuts import render, redirect
from django.contrib.auth.models import User
from django.contrib.auth import authenticate, login, logout
from django.contrib.auth.decorators import login_required
from django.views.decorators.csrf import csrf_exempt
from django.http import JsonResponse, HttpResponse
from django.conf import settings
import json
import os
import time
from .models import BlogPost
from pytube import YouTube # type: ignore
import assemblyai as aai # type: ignore
import yt_dlp # type: ignore
from langchain_ollama import OllamaLLM # type: ignore
from langchain_core.prompts import ChatPromptTemplate # type: ignore
from io import BytesIO
from xhtml2pdf import pisa # type: ignore

# Create your views here.

# This decorator ensures that only the users logged in can access the index page.
# Also, we need to define where to redirect if user is not logged in, which is the login page
# to do this, go to settings.py > LOGIN_URL = 'login'
@login_required 
def index(request):
    return render(request, 'index.html')

@csrf_exempt # After using this decorator this particular view doesn't require a csrf token
def generate_blog(request):
    if request.method == "POST":
        try:
            data = json.loads(request.body)
            ytLink = data['link']
        except (KeyError, json.JSONDecodeError):
            return JsonResponse({'error': 'Invalid data sent'}, status=400)
        
        # get yt video title
        title = yt_title(ytLink)

        # get transcript
        transcription = get_transcription(ytLink)
        if not transcription:
            return JsonResponse({'error': 'Failed to get transcript'}, status=500)

        # use llama LLM to generate the blog
        blog_content = generate_blog_from_transcription(transcription)
        if not blog_content:
            return JsonResponse({'error': 'Failed to generate blog article'}, status=500)

        # save blog article to database
        new_blog_article = BlogPost.objects.create(
            user=request.user,
            youtube_title=title,
            youtube_link=ytLink,
            generated_content=blog_content  
        )
        new_blog_article.save()

        # return blog article as a response
        return JsonResponse({'content': blog_content, 'content_id': new_blog_article.id})
    else:
        return JsonResponse({'error': 'Invalid request method'}, status=405)

def download_pdf(request):
    try:
        # Retrieve blog post based on ID from the request
        content_id = request.GET.get('content_id')
        blog_article_details = BlogPost.objects.get(id=content_id)

        # Define the HTML content with inline CSS
        html_content = f"""
        <!DOCTYPE html>
        <html lang="en">
        <head>
            <meta charset="UTF-8">
            <title>Youtube Title:{blog_article_details.youtube_title}</title>
            <style>
                body {{
                    font-family: Arial, sans-serif;
                    font-size: 12pt;
                }}
                h1 {{
                    font-size: 22pt;
                    color: #333333;
                }}
                p {{
                    font-size: 12pt;
                    margin-bottom: 10px;
                }}
                .bold {{
                    font-weight: bold;
                }}
                .italic {{
                    font-style: italic;
                }}
                .highlight {{
                    background-color: yellow;
                }}
            </style>
        </head>
        <body>
            <h1>{blog_article_details.youtube_title}</h1>
            <div>
                {blog_article_details.generated_content}
            </div>
        </body>
        </html>
        """

        # Create PDF
        result = BytesIO()
        pisa_status = pisa.CreatePDF(BytesIO(html_content.encode('utf-8')), dest=result)
        
        if pisa_status.err:
            return HttpResponse('Error generating PDF', status=500)
        
        response = HttpResponse(result.getvalue(), content_type='application/pdf')
        response['Content-Disposition'] = f'attachment; filename="{blog_article_details.youtube_title}.pdf"'
        return response

    except BlogPost.DoesNotExist:
        return HttpResponse('Blog post not found', status=404)


def yt_title(link):
    yt = YouTube(link)
    title = yt.title
    return title

def download_audio(link):
    ydl_opts = {
        'format': 'bestaudio/best',
        'postprocessors': [{
            'key': 'FFmpegExtractAudio',
            'preferredcodec': 'mp3',
            'preferredquality': '192',
        }],
        'ffmpeg_location': 'C:/Users/ASUS/Downloads/ffmpeg-2024-07-24-git-896c22ef00-full_build/ffmpeg-2024-07-24-git-896c22ef00-full_build/bin',
        'outtmpl': os.path.join(settings.MEDIA_ROOT, '%(title)s.%(ext)s'),
        'noplaylist': True,  # Ensure only a single video is processed
        'quiet': True,       # Suppress normal output
        'no_warnings': True, # Suppress warnings
    }

    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info_dict = ydl.extract_info(link, download=True)
            file_name = ydl.prepare_filename(info_dict)
            base, ext = os.path.splitext(file_name)
            new_file = base + '.mp3'
            if os.path.exists(new_file):
                return new_file
            else:
                raise Exception("Failed to convert audio to mp3.")
    except yt_dlp.utils.DownloadError as e:
        print(f"Download error: {e}")
        raise
    except yt_dlp.utils.ExtractorError as e:
        print(f"Extractor error: {e}")
        raise
    except yt_dlp.utils.PostProcessingError as e:
        print(f"Post-processing error: {e}")
        raise
    except Exception as e:
        print(f"Error downloading audio: {e}")
        raise

def get_transcription(link):
    audio_file = download_audio(link)
    aai.settings.api_key = "e7e87434850944aa986d6c6788f98081"

    transcriber = aai.Transcriber()
    transcript = transcriber.transcribe(audio_file)
    return transcript.text

def generate_blog_from_transcription(transcription):
    try:
        template = '''
        Question: Based on the following YouTube video transcript, write a comprehensive and professional article. The article should be well-structured, detailed, and formatted in HTML. 

        Your response should:
        - Use HTML tags for headings (e.g., <h1>, <h2>, <h3>), paragraphs (e.g., <p>), bold text (e.g., <strong> or <b>), italic text (e.g., <em> or <i>), line breaks (e.g., <br>), and lists (e.g., <ul>, <li>).
        - Do not use markdown or other text formatting such as **bold** or *italic*. Only use HTML tags.
        - Ensure proper nesting of HTML elements.
        - Do not include any plain text instructions or explanations, just the HTML content.

        Transcript:
        {context}

        Article (in HTML format):
        '''
        # TODO : Moondream is a vision model, replace this with lamma 3.1 LLM
        model = OllamaLLM(model="llama3.1")
        prompt = ChatPromptTemplate.from_template(template)
        chain = prompt | model
        generated_content = chain.invoke({"context": transcription})
        return generated_content
    except Exception as e:
        return str(e)  # Return the exception message for debugging



def blog_list(request):
    # Get all the generated articles for the current logged in user
    blog_articles = BlogPost.objects.filter(user=request.user)
    return render(request, "all-blogs.html", {'blog_articles': blog_articles})

def blog_details(request, pk):
    blog_article_details = BlogPost.objects.get(id=pk)
    if request.user == blog_article_details.user:
        return render(request, 'blog-details.html', {'blog_article_details': blog_article_details})
    else:
        return redirect('/')

def user_login(request):
    if request.method == "POST":
        username = request.POST['username']
        password = request.POST['password']
        
        user = authenticate(request, username=username, password=password)
        if user is not None:
            login(request, user)
            return redirect('/')
        else:
            error_message = 'Invalid Username or Password !'
            return render(request, 'login.html', {'error_message':error_message})
    return render(request, 'login.html')

def user_signup(request):
    if request.method == "POST":
        username = request.POST['username']
        email = request.POST['email']
        password = request.POST['password']
        repeatPassword = request.POST['repeatPassword']

        if password == repeatPassword:
            try:
                user = User.objects.create_user(username, email, password)
                user.save()
                login(request, user)
                return redirect('/')
            except:
                error_message = 'Error creating account !'
                return render(request, 'signup.html', {'error_message':error_message})
        else:
            error_message = 'Passwords do not match !'
            return render(request, 'signup.html', {'error_message':error_message})
    return render(request, 'signup.html')

def user_logout(request):
    logout(request)
    return redirect('/')